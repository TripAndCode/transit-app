"""What a timetable revision did to each route: its timetable on each side,
read on the typical days the stop comparison reads
(pipeline.reports.timetable_diff), and how its departures ran in the weeks
before and after.

The timetable side counts each route's trips, its first and last trip, and
the longest gap between departures at its main stop, the same stop on both
sides. The observed side pools the aggregates' exact counts
(agg_route_daily_dist) over whole weeks on each side of the revision, each
date filed under the timetable in force on it like every aggregate, so its
figures are the feed's predicted delays (pipeline.reports.definition.VALUE_BASIS).

The definitions a reader sees are in docs/methodology.md (改正の前後の路線).
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import date, timedelta
from typing import Literal, NamedTuple, cast

from pipeline import static_archive
from pipeline.cache import async_lru_cache
from pipeline.day_types import fallback_day_type, public_holidays_between
from pipeline.histogram import on_time_count_sql
from pipeline.reports.rankings import DEFAULT_EARLY_TOLERANCE_SEC, DEFAULT_LATE_TOLERANCE_SEC
from pipeline.reports.timetable_diff import (
    DAY_TYPES,
    Revision,
    RevisionDayType,
    TypicalDay,
    Unavailable,
    load_sides,
)
from pipeline.route_catalog import ROUTE_CODE_PATTERN, ROUTE_CODE_REPLACEMENT

# ── the timetable side ──────────────────────────────────────────────────────


class RouteDay(NamedTuple):
    """One route's trips on one side's typical day, with its first and last
    trip's start in seconds after the service day's midnight."""

    trips: int
    first_start: int
    last_start: int


class StopGaps(NamedTuple):
    """One route's departures from one stop on one side's typical day, and
    the longest gap between two of them in seconds; None for fewer than two."""

    departures: int
    longest_gap: int | None


# day type -> route_code -> RouteDay
RouteCounts = Mapping[RevisionDayType, Mapping[str, RouteDay]]
# day type -> route_code -> stop_id -> StopGaps
StopCounts = Mapping[RevisionDayType, Mapping[str, Mapping[str, StopGaps]]]


class RouteChange(NamedTuple):
    """One route on one day type's typical days before and after a revision.
    The gap figures are those at *main_stop*, None where the route does not
    depart from it twice on that side."""

    route_code: str
    day_type: RevisionDayType
    trips_before: int
    trips_after: int
    first_before: int | None
    first_after: int | None
    last_before: int | None
    last_after: int | None
    main_stop: str | None
    main_departures_before: int
    main_departures_after: int
    longest_gap_before: int | None
    longest_gap_after: int | None


def main_stop(before: Mapping[str, StopGaps], after: Mapping[str, StopGaps]) -> str | None:
    """The stop a route's gap is measured at on both sides: the one it departs
    from most before the revision, the lowest stop_id on a tie, or the after
    side's for a route with no departure before. One stop on both sides, so a
    gap is never compared across two stops."""
    for stops in (before, after):
        if stops:
            return min(stops, key=lambda stop_id: (-stops[stop_id].departures, stop_id))
    return None


def route_changes(
    before: RouteCounts,
    after: RouteCounts,
    before_stops: StopCounts,
    after_stops: StopCounts,
    day_types: Iterable[RevisionDayType],
) -> list[RouteChange]:
    """Every route with a trip on either side, for each of *day_types*, the
    day types both sides know."""
    changes: list[RouteChange] = []
    for day_type in day_types:
        was, now = before.get(day_type, {}), after.get(day_type, {})
        was_stops, now_stops = before_stops.get(day_type, {}), after_stops.get(day_type, {})
        for route in sorted(was.keys() | now.keys()):
            b, a = was.get(route), now.get(route)
            stop = main_stop(was_stops.get(route, {}), now_stops.get(route, {}))
            gb = was_stops.get(route, {}).get(stop) if stop is not None else None
            ga = now_stops.get(route, {}).get(stop) if stop is not None else None
            changes.append(
                RouteChange(
                    route,
                    day_type,
                    b.trips if b else 0,
                    a.trips if a else 0,
                    b.first_start if b else None,
                    a.first_start if a else None,
                    b.last_start if b else None,
                    a.last_start if a else None,
                    stop,
                    gb.departures if gb else 0,
                    ga.departures if ga else 0,
                    gb.longest_gap if gb else None,
                    ga.longest_gap if ga else None,
                )
            )
    return changes


RouteClass = Literal["cut", "added", "unchanged", "new", "removed"]


def route_classes(changes: Iterable[RouteChange]) -> dict[str, RouteClass]:
    """Each route's class from its trips on the typical days of the one day
    type *changes* hold: new with none before, removed with none after, else
    cut, added or unchanged as they fell, rose or stayed. Classed per day
    type, so a route cut only on Saturdays is unchanged on weekdays."""
    classes: dict[str, RouteClass] = {}
    for c in changes:
        b, a = c.trips_before, c.trips_after
        if b == 0:
            classes[c.route_code] = "new"
        elif a == 0:
            classes[c.route_code] = "removed"
        elif a < b:
            classes[c.route_code] = "cut"
        elif a > b:
            classes[c.route_code] = "added"
        else:
            classes[c.route_code] = "unchanged"
    return classes


_ROUTE_CODE_CH = "replaceRegexpOne(assumeNotNull(s.route_id), {pattern:String}, {replacement:String})"
_DAY_TYPE_JOIN = (
    "ARRAY JOIN arrayFilter(i -> has({services:Array(Array(String))}[i], service), "
    f"range(1, {len(DAY_TYPES) + 1})) AS dt"
)

# A trip starts at its earliest timed departure; a trip with no route, service
# or timed stop is no route's trip.
_ROUTE_TRIPS_SQL = f"""
    SELECT dt, route_code, toInt64(count()), min(start_sec), max(start_sec)
    FROM (
        SELECT s.trip_id AS trip_id, any({_ROUTE_CODE_CH}) AS route_code,
               any(assumeNotNull(s.service_type)) AS service, min(assumeNotNull(s.scheduled_sec)) AS start_sec
        FROM {static_archive.SCHEDULE} AS s
        WHERE s.agency_id = {{agency:Int32}} AND s.content_hash = {{content:String}}
          AND s.route_id IS NOT NULL AND s.service_type IS NOT NULL AND s.scheduled_sec IS NOT NULL
        GROUP BY s.trip_id
    )
    {_DAY_TYPE_JOIN}
    GROUP BY dt, route_code
"""

# A departure as the stop comparison counts one: not at the trip's last stop,
# and at a timed stop.
_STOP_GAPS_SQL = f"""
    SELECT dt, route_code, stop_id, toInt64(length(secs)),
           if(length(secs) < 2, NULL, arrayMax(arrayPopFront(arrayDifference(secs))))
    FROM (
        SELECT dt, route_code, stop_id, arraySort(groupArray(sec)) AS secs
        FROM (
            SELECT st.stop_id AS stop_id, {_ROUTE_CODE_CH} AS route_code,
                   assumeNotNull(s.service_type) AS service, assumeNotNull(s.scheduled_sec) AS sec
            FROM {static_archive.SCHEDULE} AS s
            INNER JOIN (
                SELECT trip_id, stop_sequence, stop_id,
                       max(stop_sequence) OVER (PARTITION BY trip_id) AS last_sequence
                FROM {static_archive.ARCHIVE}
                WHERE agency_id = {{agency:Int32}} AND content_hash = {{content:String}}
            ) AS st ON st.trip_id = s.trip_id AND st.stop_sequence = s.stop_sequence
            WHERE s.agency_id = {{agency:Int32}} AND s.content_hash = {{content:String}}
              AND s.route_id IS NOT NULL AND s.service_type IS NOT NULL AND s.scheduled_sec IS NOT NULL
              AND st.stop_sequence < st.last_sequence
        )
        {_DAY_TYPE_JOIN}
        GROUP BY dt, route_code, stop_id
    )
"""


def route_counts_of(rows: Iterable[tuple]) -> dict[RevisionDayType, dict[str, RouteDay]]:
    """_ROUTE_TRIPS_SQL's rows as RouteCounts."""
    out: dict[RevisionDayType, dict[str, RouteDay]] = {day_type: {} for day_type in DAY_TYPES}
    for index, route, trips, first, last in rows:
        out[DAY_TYPES[index - 1]][route] = RouteDay(int(trips), int(first), int(last))
    return out


def stop_counts_of(rows: Iterable[tuple]) -> dict[RevisionDayType, dict[str, dict[str, StopGaps]]]:
    """_STOP_GAPS_SQL's rows as StopCounts."""
    out: dict[RevisionDayType, dict[str, dict[str, StopGaps]]] = {day_type: {} for day_type in DAY_TYPES}
    for index, route, stop_id, departures, gap in rows:
        out[DAY_TYPES[index - 1]].setdefault(route, {})[stop_id] = StopGaps(
            int(departures), None if gap is None else int(gap)
        )
    return out


async def _query(ch, sql: str, agency_id: int, content: str, days: Mapping[RevisionDayType, TypicalDay | None]):
    services = [sorted(day.services) if (day := days.get(day_type)) is not None else [] for day_type in DAY_TYPES]
    if not any(services):
        return []
    parameters = {
        "agency": agency_id,
        "content": content,
        "services": services,
        "pattern": ROUTE_CODE_PATTERN,
        "replacement": ROUTE_CODE_REPLACEMENT,
    }
    return (await ch.query(sql, parameters=parameters)).result_rows


class RouteTimetables(NamedTuple):
    revision: Revision
    known: tuple[RevisionDayType, ...]
    changes: tuple[RouteChange, ...]

    def on(self, day_type: RevisionDayType) -> tuple[list[RouteChange], dict[str, RouteClass]]:
        """The routes of *day_type*, and their classes on it."""
        changes = [c for c in self.changes if c.day_type == day_type]
        return changes, route_classes(changes)


# Keyed by the revision, which fixes its timetables (load_comparison's cache).
@async_lru_cache(maxsize=16, ttl_seconds=24 * 3600, agency_arg="agency_id", rebuild="never")
async def load_route_timetables(ch, agency_id: int, revision: Revision) -> RouteTimetables | Unavailable:
    """Every route's timetable on both sides of *revision*, or why there is
    none (timetable_diff.load_sides). Shared by every caller; not to be modified."""
    sides = await load_sides(ch, agency_id, revision)
    if isinstance(sides, Unavailable):
        return sides
    known = tuple(dt for dt in DAY_TYPES if sides.before.get(dt) is not None and sides.after.get(dt) is not None)
    before, after, before_stops, after_stops = await asyncio.gather(
        _query(ch, _ROUTE_TRIPS_SQL, agency_id, revision.before.content, sides.before),
        _query(ch, _ROUTE_TRIPS_SQL, agency_id, revision.after.content, sides.after),
        _query(ch, _STOP_GAPS_SQL, agency_id, revision.before.content, sides.before),
        _query(ch, _STOP_GAPS_SQL, agency_id, revision.after.content, sides.after),
    )
    changes = route_changes(
        route_counts_of(before),
        route_counts_of(after),
        stop_counts_of(before_stops),
        stop_counts_of(after_stops),
        known,
    )
    return RouteTimetables(revision, known, tuple(changes))


# ── the observed side ───────────────────────────────────────────────────────

# How many whole weeks on each side the observed figures pool at most: four,
# enough for a month of each weekday to settle.
WEEKS = 4


class Windows(NamedTuple):
    """The service dates pooled on each side, both ends inclusive: *weeks*
    whole weeks each, the before side ending on the last day of the timetable
    before the revision and the after side starting on its first day."""

    weeks: int
    before: tuple[date, date]
    after: tuple[date, date]

    @property
    def complete(self) -> bool:
        return self.weeks == WEEKS


def windows(revision: Revision, last_data_day: date | None) -> Windows | None:
    """The weeks pooled on each side of *revision*: as many whole weeks, up to
    WEEKS, as both the timetable before it and the aggregated days after it,
    which end on *last_data_day*, hold. None when either holds less than one."""
    if last_data_day is None:
        return None
    before_weeks = ((revision.before.last - revision.before.first).days + 1) // 7
    after_end = min(revision.after.last, last_data_day)
    after_weeks = ((after_end - revision.first_day).days + 1) // 7
    weeks = min(WEEKS, before_weeks, after_weeks)
    if weeks < 1:
        return None
    span = timedelta(days=7 * weeks - 1)
    return Windows(
        weeks, (revision.before.last - span, revision.before.last), (revision.first_day, revision.first_day + span)
    )


def full_through(revision: Revision) -> date:
    """The last date the after side needs aggregated for WEEKS whole weeks."""
    return revision.first_day + timedelta(days=7 * WEEKS - 1)


class Tally(NamedTuple):
    """Pooled exact counts: departures observed, those on time under the
    default window, and the sum of their delays in seconds."""

    observations: int = 0
    on_time: int = 0
    sum_delay_sec: int = 0

    def plus(self, other: Tally) -> Tally:
        return Tally(*(a + b for a, b in zip(self, other, strict=True)))

    @property
    def on_time_pct(self) -> float | None:
        return None if self.observations == 0 else 100 * self.on_time / self.observations

    @property
    def mean_delay_sec(self) -> float | None:
        return None if self.observations == 0 else self.sum_delay_sec / self.observations


Side = Literal["before", "after"]

# (side, day type, route_code) -> Tally
Observed = Mapping[tuple[Side, RevisionDayType, str], Tally]


def pool(rows: Iterable[tuple], spans: Windows) -> dict[tuple[Side, RevisionDayType, str], Tally]:
    """Per-date, per-route rows (date, route_code, observations, on_time,
    sum_delay_sec) pooled by side and by the weekday rule's day type of each
    date. A date in neither window is left out."""
    holidays = frozenset(public_holidays_between(spans.before[0], spans.after[1]))
    out: dict[tuple[Side, RevisionDayType, str], Tally] = defaultdict(Tally)
    for day, route, observations, on_time, sum_delay in rows:
        if spans.before[0] <= day <= spans.before[1]:
            side: Side = "before"
        elif spans.after[0] <= day <= spans.after[1]:
            side = "after"
        else:
            continue
        day_type = cast(RevisionDayType, fallback_day_type(day, holidays))
        key = (side, day_type, route)
        out[key] = out[key].plus(Tally(int(observations), int(on_time), int(sum_delay)))
    return out


class ClassTally(NamedTuple):
    route_class: RouteClass
    routes: int
    before: Tally
    after: Tally


# The classes compared: a new or removed route has no side to compare against.
COMPARED: tuple[RouteClass, ...] = ("cut", "added", "unchanged")


def class_tallies(observed: Observed, classes: Mapping[str, RouteClass], day_type: RevisionDayType) -> list[ClassTally]:
    """Each compared class's counts on *day_type*, pooled over its routes
    observed on both sides; a route observed on one side only is left out of
    both, so the two sides pool the same routes."""
    out = []
    for route_class in COMPARED:
        routes = [
            r
            for r, c in classes.items()
            if c == route_class
            and observed.get(("before", day_type, r), Tally()).observations
            and observed.get(("after", day_type, r), Tally()).observations
        ]
        before, after = Tally(), Tally()
        for r in routes:
            before = before.plus(observed[("before", day_type, r)])
            after = after.plus(observed[("after", day_type, r)])
        out.append(ClassTally(route_class, len(routes), before, after))
    return out


_ON_TIME = on_time_count_sql(DEFAULT_EARLY_TOLERANCE_SEC, DEFAULT_LATE_TOLERANCE_SEC)
_OBSERVED_SQL = f"""
    SELECT date, route_code, SUM(samples), SUM({_ON_TIME}), SUM(sum_delay_sec)
    FROM agg_route_daily_dist
    WHERE agency_id = $1 AND (date BETWEEN $2 AND $3 OR date BETWEEN $4 AND $5)
    GROUP BY date, route_code
"""


async def last_aggregated_day(conn, agency_id: int) -> date | None:
    return await conn.fetchval("SELECT max(date) FROM agg_route_daily_dist WHERE agency_id = $1", agency_id)


@async_lru_cache(maxsize=64, ttl_seconds=300, agency_arg="agency_id")
async def load_observed(conn, agency_id: int, spans: Windows) -> dict[tuple[Side, RevisionDayType, str], Tally]:
    """The agency's pooled counts over *spans*. Cached like the reports and
    dropped when the agency's aggregates rebuild; the windows are in the key,
    so a window grown by a day is read again."""
    rows = await conn.fetch(_OBSERVED_SQL, agency_id, *spans.before, *spans.after)
    return pool(((r[0], r[1], r[2], r[3], r[4]) for r in rows), spans)
