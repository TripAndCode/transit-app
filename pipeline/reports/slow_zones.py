"""Slow zones: timetable segments whose running time stays over the timetable
for weeks, read from the running time per timetable segment
(`agg_route_segment_runtime`, read for one route by
`pipeline.reports.segment_runtime`). docs/methodology.md defines each figure.

A segment here is an agency's stop pair, pooled over every route that runs
it, for one time band and one day type. Each of the last ``WINDOW_WEEKS``
complete weeks (Monday to Sunday) gets the observed running time's median
and the scheduled running time of the same trip-days. A week is slow when the
median exceeds the timetable by ``SLOW_EXCESS_SEC`` or more, on at least
``MIN_WEEK_SAMPLES`` runs of a segment the timetable gives at least
``MIN_SCHEDULED_SEC``; a segment is a slow zone when its newest
``MIN_SLOW_WEEKS`` or more weeks were all slow.

Distance is the straight line between the two stops of the loaded timetable,
so the speed it gives is a lower bound on a winding road. No slow-zone rule
reads the distance.

Every figure is the feed's prediction (`value_basis`), not an observed
passage, until the agency's predictions are validated against observed
positions. Segments are not ranked as bus-priority candidates here.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Literal

from api.range import TIME_BAND_RANGES
from pipeline import perf
from pipeline.cache import async_lru_cache
from pipeline.day_types import fallback_day_type, public_holidays_between
from pipeline.reports.definition import VALUE_BASIS
from pipeline.reports.rankings import MIN_GROUP_SAMPLES
from pipeline.strategies.static_join import rt_field_coverage_gap

# How many complete weeks a read covers, and so the longest streak it can see.
WINDOW_WEEKS = 8
# A minute: a timetable written to the minute can put up to just under a
# minute on one segment and take it off the next by rounding alone.
SLOW_EXCESS_SEC = 60
# A segment the timetable gives less than a minute is one whose scheduled
# time is mostly rounding, so its excess says little about the road.
MIN_SCHEDULED_SEC = 60
MIN_SLOW_WEEKS = 3
MIN_WEEK_SAMPLES = MIN_GROUP_SAMPLES

SlowZoneDayType = Literal["weekday", "weekend_holiday"]
# The calendar day types (pipeline.day_types.fallback_day_type) each reads.
_DAY_TYPE_DATES: dict[SlowZoneDayType, frozenset[str]] = {
    "weekday": frozenset({"weekday"}),
    "weekend_holiday": frozenset({"saturday", "sunday_holiday"}),
}


@dataclass(frozen=True)
class SegmentWeek:
    """One segment's running time over one week's dates of the day type.
    ``days`` is how many of those dates have a run of it."""

    samples: int
    days: int
    run_p50_sec: float | None
    run_p85_sec: float | None
    scheduled_run_sec: float | None

    @property
    def excess_sec(self) -> float | None:
        """The median running time minus the scheduled one (the negative of
        segment_runtime's ``padding_sec``)."""
        if self.run_p50_sec is None or self.scheduled_run_sec is None:
            return None
        return self.run_p50_sec - self.scheduled_run_sec

    @property
    def enough_samples(self) -> bool:
        return self.samples >= MIN_WEEK_SAMPLES


def speed_kmh(distance_m: float | None, run_sec: float | None) -> float | None:
    """The speed of covering *distance_m* in *run_sec*; None without either,
    or when either is not positive."""
    if distance_m is None or run_sec is None or distance_m <= 0 or run_sec <= 0:
        return None
    return distance_m / run_sec * 3.6


def is_slow_week(week: SegmentWeek | None) -> bool:
    """Whether *week* (None: not observed) counts toward a slow zone."""
    if week is None or not week.enough_samples:
        return False
    if week.scheduled_run_sec is None or week.scheduled_run_sec < MIN_SCHEDULED_SEC:
        return False
    excess = week.excess_sec
    return excess is not None and excess >= SLOW_EXCESS_SEC


def slow_streak(weeks: Sequence[SegmentWeek | None]) -> int:
    """How many of the newest *weeks* (oldest first, one per window week) were
    slow in a row. A week not observed, or observed too little, ends it: it
    cannot show the segment stayed slow."""
    streak = 0
    for week in reversed(weeks):
        if not is_slow_week(week):
            break
        streak += 1
    return streak


def trips_per_day(week: SegmentWeek) -> float | None:
    """Observed runs per date with a run: the feed's gaps make it short of
    the timetable's trips."""
    return week.samples / week.days if week.days else None


def minutes_lost_per_day(week: SegmentWeek) -> float | None:
    """The excess, at least zero, times the runs per day, in minutes."""
    excess, per_day = week.excess_sec, trips_per_day(week)
    if excess is None or per_day is None:
        return None
    return max(excess, 0.0) * per_day / 60


def window_weeks(last_day: date) -> list[date]:
    """The Mondays of the ``WINDOW_WEEKS`` complete weeks ending on or before
    *last_day*, oldest first. The week *last_day* is in counts only when
    *last_day* is its Sunday."""
    last_sunday = last_day - timedelta(days=last_day.isoweekday() % 7)
    newest = last_sunday - timedelta(days=6)
    return [newest - timedelta(weeks=i) for i in reversed(range(WINDOW_WEEKS))]


def window_dates(weeks: Sequence[date], day_type: SlowZoneDayType) -> list[date]:
    """The dates of *weeks* (their Mondays) that are of *day_type* by the
    calendar, oldest first."""
    if not weeks:
        return []
    first, last = weeks[0], weeks[-1] + timedelta(days=6)
    holidays = set(public_holidays_between(first, last))
    wanted = _DAY_TYPE_DATES[day_type]
    days = (first + timedelta(days=i) for i in range((last - first).days + 1))
    return [d for d in days if fallback_day_type(d, holidays) in wanted]


@dataclass(frozen=True)
class SlowZoneSegment:
    from_stop_id: str
    to_stop_id: str
    from_stop_name: str | None
    to_stop_name: str | None
    # From the loaded timetable; None for a stop it lacks or has no position for.
    from_lon: float | None
    from_lat: float | None
    to_lon: float | None
    to_lat: float | None
    distance_m: float | None
    routes: list[str]
    # One per window week, oldest first; None for a week with no run.
    weeks: list[SegmentWeek | None]
    slow_weeks: int = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "slow_weeks", slow_streak(self.weeks))

    @property
    def latest(self) -> SegmentWeek | None:
        return self.weeks[-1] if self.weeks else None

    @property
    def is_slow_zone(self) -> bool:
        return self.slow_weeks >= MIN_SLOW_WEEKS


@dataclass(frozen=True)
class SlowZones:
    """``available`` False means the agency's arrival data is not trusted,
    for ``unavailable_reason`` (``pipeline.strategies.static_join.CoverageGap``)."""

    available: bool
    unavailable_reason: str | None
    last_checked: str | None
    day_type: SlowZoneDayType
    time_band: str
    weeks: list[date]
    segments: list[SlowZoneSegment]
    value_basis: str = VALUE_BASIS

    @property
    def slow_zones(self) -> list[SlowZoneSegment]:
        """The slow zones, longest-standing first. Not a ranking by minutes
        lost: that waits for the agency's predictions to be validated."""
        return sorted(
            (s for s in self.segments if s.is_slow_zone),
            key=lambda s: (-s.slow_weeks, s.from_stop_id, s.to_stop_id),
        )


_SQL = """
WITH weeks AS (
    -- The timetable in force on each week's last date read: a week spanning
    -- a revision is read under the newer timetable alone.
    SELECT w.week_start,
           (SELECT r.static_version_id FROM agg_schedule_revision_daily r
            WHERE r.agency_id = $1 AND r.date <= w.last_day
            ORDER BY r.date DESC LIMIT 1) AS static_version_id
    FROM unnest($3::date[], $4::date[]) AS w(week_start, last_day)
),
kept AS (
    SELECT s.from_stop_id, s.to_stop_id, s.route_code, s.date, w.week_start,
           s.run_sec, s.scheduled_samples, s.scheduled_sum_sec
    FROM agg_route_segment_runtime s
    JOIN weeks w ON w.week_start = date_trunc('week', s.date)::date
    LEFT JOIN agg_schedule_revision_daily r ON r.agency_id = s.agency_id AND r.date = s.date
    WHERE s.agency_id = $1 AND s.date = ANY($2::date[]) AND s.time_band = $5
      AND r.static_version_id IS NOT DISTINCT FROM w.static_version_id
),
pooled AS (
    SELECT from_stop_id, to_stop_id, week_start,
           COUNT(DISTINCT date) FILTER (WHERE cardinality(run_sec) > 0) AS days,
           SUM(scheduled_samples) AS scheduled_samples, SUM(scheduled_sum_sec) AS scheduled_sum_sec,
           array_agg(DISTINCT route_code) AS routes
    FROM kept GROUP BY from_stop_id, to_stop_id, week_start
),
runs AS (
    SELECT from_stop_id, to_stop_id, week_start, COUNT(*) AS samples,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY x) AS p50,
           percentile_cont(0.85) WITHIN GROUP (ORDER BY x) AS p85
    FROM kept, unnest(run_sec) AS x
    GROUP BY from_stop_id, to_stop_id, week_start
)
SELECT p.from_stop_id, p.to_stop_id, p.week_start, p.days, p.scheduled_samples, p.scheduled_sum_sec, p.routes,
       COALESCE(r.samples, 0) AS samples, r.p50, r.p85,
       fs.stop_name AS from_stop_name, ST_X(fs.geom) AS from_lon, ST_Y(fs.geom) AS from_lat,
       ts.stop_name AS to_stop_name, ST_X(ts.geom) AS to_lon, ST_Y(ts.geom) AS to_lat,
       ST_Distance(fs.geom::geography, ts.geom::geography) AS distance_m
FROM pooled p
LEFT JOIN runs r USING (from_stop_id, to_stop_id, week_start)
LEFT JOIN static_stops fs ON fs.agency_id = $1 AND fs.stop_id = p.from_stop_id
LEFT JOIN static_stops ts ON ts.agency_id = $1 AND ts.stop_id = p.to_stop_id
"""


def _float(value) -> float | None:
    return float(value) if value is not None else None


@perf.timed("reports.slow_zones")
@async_lru_cache(maxsize=64, ttl_seconds=300, agency_arg="agency_id")
async def read_slow_zones(
    agency_id: int, conn, *, last_day: date, day_type: SlowZoneDayType, time_band: str
) -> SlowZones:
    """Every segment of *agency_id* run in *time_band* on *day_type* dates of
    the ``WINDOW_WEEKS`` complete weeks ending by *last_day*, with its slow
    streak. Gated, like segment_runtime, on the agency's field-coverage
    verdict."""
    if time_band not in TIME_BAND_RANGES:
        raise ValueError(f"unknown time band: {time_band}")
    weeks = window_weeks(last_day)
    gap = await rt_field_coverage_gap(agency_id, conn)
    if gap is not None:
        payload = gap.payload()
        return SlowZones(False, payload["unavailable_reason"], payload["last_checked"], day_type, time_band, weeks, [])
    dates = window_dates(weeks, day_type)
    last_read: dict[date, date] = {}
    for d in dates:
        last_read[d - timedelta(days=d.weekday())] = d
    read_weeks = [w for w in weeks if w in last_read]
    rows = await conn.fetch(
        _SQL,
        agency_id,
        dates,
        read_weeks,
        [last_read[w] for w in read_weeks],
        time_band,
    )
    by_pair: dict[tuple[str, str], list] = {}
    for r in rows:
        by_pair.setdefault((r["from_stop_id"], r["to_stop_id"]), []).append(r)
    position = {w: i for i, w in enumerate(weeks)}
    segments = []
    for (from_stop, to_stop), pair_rows in by_pair.items():
        series: list[SegmentWeek | None] = [None] * len(weeks)
        routes: set[str] = set()
        for r in pair_rows:
            routes.update(r["routes"])
            if not r["samples"]:
                continue
            series[position[r["week_start"]]] = SegmentWeek(
                samples=r["samples"],
                days=r["days"],
                run_p50_sec=_float(r["p50"]),
                run_p85_sec=_float(r["p85"]),
                scheduled_run_sec=(
                    float(r["scheduled_sum_sec"] / r["scheduled_samples"]) if r["scheduled_samples"] else None
                ),
            )
        first = pair_rows[0]
        segments.append(
            SlowZoneSegment(
                from_stop_id=from_stop,
                to_stop_id=to_stop,
                from_stop_name=first["from_stop_name"],
                to_stop_name=first["to_stop_name"],
                from_lon=_float(first["from_lon"]),
                from_lat=_float(first["from_lat"]),
                to_lon=_float(first["to_lon"]),
                to_lat=_float(first["to_lat"]),
                distance_m=_float(first["distance_m"]),
                routes=sorted(routes),
                weeks=series,
            )
        )
    segments.sort(key=lambda s: (s.from_stop_id, s.to_stop_id))
    return SlowZones(True, None, None, day_type, time_band, weeks, segments)
