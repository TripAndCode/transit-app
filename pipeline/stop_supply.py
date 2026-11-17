"""Planned supply at every stop: the departures each timetable version in
force plans there in every hour of a typical day of each day type, with the
first and last of them and the longest wait between two within the hour.

The timetables are the archive's (pipeline.static_archive): each version in
force over the agency's service dates (pipeline.timetable_in_force) and its
kept stop times, schedule and calendar. Each version is read on a typical
day of each day type (pipeline.timetable_sampling), chosen among the first
dates it is in force on, so operators whose calendars split the week
differently still read alike on one map. A version whose calendar is not kept
has every type unknown, which is never read as zero.

Departures and their service-day seconds are timetable_sampling's: 00:40
written at face value is the same night's 24:40.

A run re-reads a version's calendar only when the version is new, the dates
it is in force on moved, or it had no kept calendar and now has one: the
segments it was read over are kept beside its typical days.

The definitions a reader sees are in docs/methodology.md (停留所の運行本数).
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, timedelta
from itertools import pairwise
from typing import NamedTuple

import psycopg2.extras

from pipeline import static_archive, timetable_sampling
from pipeline.day_types import ServiceCalendar, WeeklyService, calendar_dates_day, fallback_day_type
from pipeline.timetable_sampling import DAY_TYPES, MAX_WALK_DAYS, TypicalDay, service_calendar
from pipeline.timetable_sampling import DayType as SupplyDayType

logger = logging.getLogger(__name__)

TABLE = "agg_stop_supply_hourly"
DAYS_TABLE = "agg_stop_supply_days"


class VersionInForce(NamedTuple):
    """A version name at one content and the runs of service dates, both ends
    inclusive and in date order, on which it is in force."""

    stem: str
    content: str
    segments: tuple[tuple[date, date], ...]


def versions_in_force(segments: Iterable[tuple[date, date, str, str]]) -> list[VersionInForce]:
    """*segments*, ScheduleJoin.in_force's (first, last, version name, content),
    gathered by version name and content: a version can be in force over more
    than one run when another is in force between them."""
    runs: dict[tuple[str, str], list[tuple[date, date]]] = {}
    for first, last, stem, content in segments:
        runs.setdefault((stem, content), []).append((first, last))
    return [VersionInForce(stem, content, tuple(sorted(spans))) for (stem, content), spans in runs.items()]


def typical_days(
    segments: Sequence[tuple[date, date]], calendar: ServiceCalendar
) -> dict[SupplyDayType, TypicalDay | None]:
    """The typical day of each day type of a version in force over *segments*,
    sought among its dates in force, earliest first, up to MAX_WALK_DAYS past
    the first of them within *calendar*'s extent."""
    out: dict[SupplyDayType, TypicalDay | None] = dict.fromkeys(DAY_TYPES)
    if calendar.extent is None:
        return out
    lo_extent, hi_extent = calendar.extent
    clipped = [(max(first, lo_extent), min(last, hi_extent)) for first, last in segments]
    clipped = sorted((first, last) for first, last in clipped if first <= last)
    if not clipped:
        return out
    lo = clipped[0][0]
    hi = min(clipped[-1][1], lo + timedelta(days=MAX_WALK_DAYS))
    walk = (first + timedelta(days=i) for first, last in clipped for i in range((min(last, hi) - first).days + 1))
    return timetable_sampling.typical_days(walk, calendar, (lo, hi))


class HourCell(NamedTuple):
    """One stop's departures in one service-day hour: how many, the first and
    last in service-day seconds, and the longest wait between two consecutive
    ones of the same hour, None with fewer than two."""

    departures: int
    first_sec: int
    last_sec: int
    max_gap_sec: int | None


def hour_cells(departure_secs: Iterable[int]) -> dict[int, HourCell]:
    """One stop's departures on one day, in service-day seconds, as HourCells
    by service-day hour."""
    secs = sorted(departure_secs)
    by_hour: dict[int, list[int]] = {}
    for sec in secs:
        by_hour.setdefault(sec // 3600, []).append(sec)
    return {
        hour: HourCell(len(times), times[0], times[-1], max((b - a for a, b in pairwise(times)), default=None))
        for hour, times in by_hour.items()
    }


class BandSupply(NamedTuple):
    """One stop's supply over a band of hours: its departures, their mean per
    hour, and the longest stretch of the band with no departure."""

    departures: int
    per_hour: float
    longest_gap_sec: int


def band_supply(cells: Mapping[int, HourCell], from_hour: int, to_hour: int) -> BandSupply:
    """The supply of a stop's *cells* over service-day hours [*from_hour*,
    *to_hour*). The longest gap is the longest stretch of the band with no
    departure: between two consecutive departures within it, from its start
    to its first departure, or from its last departure to its end, and the
    whole band when it holds none. Departures outside the band do not stretch
    a gap past its edges, so the gap never exceeds the band."""
    if to_hour <= from_hour:
        raise ValueError(f"empty band [{from_hour}, {to_hour})")
    start, end = from_hour * 3600, to_hour * 3600
    hours = sorted(hour for hour in cells if from_hour <= hour < to_hour)
    if not hours:
        return BandSupply(0, 0.0, end - start)
    departures = sum(cells[hour].departures for hour in hours)
    gaps = [cells[hours[0]].first_sec - start, end - cells[hours[-1]].last_sec]
    gaps += [cell.max_gap_sec for hour in hours if (cell := cells[hour]).max_gap_sec is not None]
    gaps += [cells[later].first_sec - cells[earlier].last_sec for earlier, later in pairwise(hours)]
    return BandSupply(departures, departures / (to_hour - from_hour), max(gaps))


_HOURLY_COLUMNS = (
    "agency_id",
    "static_version_id",
    "content_hash",
    "day_type",
    "stop_id",
    "hour",
    "departures",
    "first_sec",
    "last_sec",
    "max_gap_sec",
)


def render() -> list[str]:
    """What decides agg_stop_supply_hourly's rows beyond the typical days
    agg_stop_supply_days holds, which it depends on."""
    return [
        timetable_sampling.DEPARTURE_SECONDS_SQL,
        inspect.getsource(timetable_sampling.services_by_day_type),
        inspect.getsource(HourCell),
        inspect.getsource(hour_cells),
        inspect.getsource(hourly_rows),
        inspect.getsource(_build),
        repr(_HOURLY_COLUMNS),
    ]


def render_days() -> list[str]:
    """What decides agg_stop_supply_days' rows: each version's typical days."""
    return [
        inspect.getsource(versions_in_force),
        inspect.getsource(_read_days),
        inspect.getsource(typical_days),
        inspect.getsource(timetable_sampling.typical_days),
        inspect.getsource(timetable_sampling.typical_day),
        inspect.getsource(timetable_sampling._quota),
        inspect.getsource(timetable_sampling.service_calendar),
        inspect.getsource(ServiceCalendar),
        inspect.getsource(WeeklyService),
        inspect.getsource(calendar_dates_day),
        inspect.getsource(fallback_day_type),
        f"SAMPLE_DATES={timetable_sampling.SAMPLE_DATES!r}",
        f"MAX_WALK_DAYS={MAX_WALK_DAYS!r}",
    ]


Days = dict[SupplyDayType, TypicalDay | None]


def segments_digest(segments: Iterable[tuple[date, date]]) -> str:
    """The dates a version is in force on, as text a run compares with the
    last one's to tell whether its typical days can have moved."""
    return ",".join(f"{first.isoformat()}/{last.isoformat()}" for first, last in segments)


class _Stored(NamedTuple):
    days: dict[str, date | None]
    segments: str
    calendar_read: bool


def _stored(conn, agency_id: int) -> dict[tuple[str, str], _Stored]:
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT static_version_id, content_hash, day_type, typical_day, segments, calendar_read FROM {DAYS_TABLE} "
            "WHERE agency_id = %s",
            (agency_id,),
        )
        out: dict[tuple[str, str], _Stored] = {}
        for stem, content, day_type, day, segments, calendar_read in cur.fetchall():
            out.setdefault((stem, content), _Stored({}, segments, calendar_read)).days[day_type] = day
        return out


def _days_of(days: Days) -> dict[str, date | None]:
    return {day_type: None if typical is None else typical.day for day_type, typical in days.items()}


def _read_days(ch, agency_id: int, version: VersionInForce, kept: set[tuple[str, str]]) -> tuple[Days, bool]:
    """The version's typical days, and whether its kept calendar was read for
    them: without one every type is unknown."""
    key = (version.stem, version.content)
    archived = static_archive.archived_calendar(ch, agency_id, *key) if key in kept else None
    if archived is None:
        return dict.fromkeys(DAY_TYPES), False
    return typical_days(version.segments, service_calendar(archived)), True


def hourly_rows(agency_id: int, stem: str, content: str, rows: Iterable[tuple]) -> list[tuple]:
    """timetable_sampling.DEPARTURE_SECONDS_SQL's rows, (day type index,
    stop_id, departure seconds), as agg_stop_supply_hourly rows of version
    *stem* at *content*."""
    out = []
    for index, stop_id, secs in rows:
        day_type = DAY_TYPES[index - 1]
        for hour, cell in sorted(hour_cells(secs).items()):
            out.append((agency_id, stem, content, day_type, stop_id, hour, *cell))
    return out


def _build(conn, ch, agency_id: int, version: VersionInForce, days: Days, calendar_read: bool) -> int:
    services = timetable_sampling.services_by_day_type(days)
    rows: list[tuple] = []
    if any(services):
        parameters = {"agency": agency_id, "content": version.content, "services": services}
        result = ch.query(timetable_sampling.DEPARTURE_SECONDS_SQL, parameters=parameters)
        rows = hourly_rows(agency_id, version.stem, version.content, result.result_rows)
    digest = segments_digest(version.segments)
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(
            cur,
            f"INSERT INTO {DAYS_TABLE} "
            "(agency_id, static_version_id, content_hash, day_type, typical_day, segments, calendar_read) VALUES %s",
            [
                (agency_id, version.stem, version.content, dt, day, digest, calendar_read)
                for dt, day in _days_of(days).items()
            ],
        )
        if rows:
            psycopg2.extras.execute_values(
                cur, f"INSERT INTO {TABLE} ({', '.join(_HOURLY_COLUMNS)}) VALUES %s", rows, page_size=5000
            )
    return len(rows)


def _delete(conn, agency_id: int, versions: Iterable[tuple[str, str]]) -> None:
    keys = list(versions)
    if not keys:
        return
    with conn.cursor() as cur:
        for table in (DAYS_TABLE, TABLE):
            cur.execute(
                f"DELETE FROM {table} WHERE agency_id = %s AND (static_version_id, content_hash) IN "
                "(SELECT * FROM unnest(%s::text[], %s::text[]))",
                (agency_id, [stem for stem, _ in keys], [content for _, content in keys]),
            )


def _record_read(conn, agency_id: int, version: VersionInForce, calendar_read: bool) -> None:
    """Record that the version's rows stand as read over its dates in force now."""
    with conn.cursor() as cur:
        cur.execute(
            f"UPDATE {DAYS_TABLE} SET segments = %s, calendar_read = %s WHERE agency_id = %s "
            "AND static_version_id = %s AND content_hash = %s",
            (segments_digest(version.segments), calendar_read, agency_id, version.stem, version.content),
        )


def rebuild(conn, ch, agency_id: int, *, full: bool) -> bool:
    """Bring the agency's rows to the versions in force now, in *conn*'s open
    transaction, without committing; whether any row changed.

    *full* rebuilds every version. Otherwise a version is built when it is new
    or its typical day of some type moved, its rows go when it is no longer in
    force, and its calendar is read only when its dates in force moved or it
    gained a kept calendar since it was built without one. A run that reads a
    calendar and finds the typical days as they stand records the read
    without rebuilding, so the next run reads none.
    """
    join = static_archive.schedule_join(ch, agency_id, None, by_service_date=True)
    versions = versions_in_force(join.in_force())
    kept = static_archive.calendar_versions(ch, agency_id)
    stored: dict[tuple[str, str], _Stored] = {}
    if full:
        with conn.cursor() as cur:
            for table in (DAYS_TABLE, TABLE):
                cur.execute(f"DELETE FROM {table} WHERE agency_id = %s", (agency_id,))
    else:
        stored = _stored(conn, agency_id)
    in_force = {(v.stem, v.content) for v in versions}
    gone = [key for key in stored if key not in in_force]
    _delete(conn, agency_id, gone)
    changed = bool(gone)
    for version in versions:
        key = (version.stem, version.content)
        was = stored.get(key)
        if (
            was is not None
            and was.segments == segments_digest(version.segments)
            and (was.calendar_read or key not in kept)
        ):
            continue
        days, calendar_read = _read_days(ch, agency_id, version, kept)
        if was is not None and was.days == _days_of(days):
            _record_read(conn, agency_id, version, calendar_read)
            continue
        _delete(conn, agency_id, [key])
        count = _build(conn, ch, agency_id, version, days, calendar_read)
        changed = True
        known = [day_type for day_type, typical in days.items() if typical is not None]
        logger.info(f"  {TABLE}: {version.stem} {count:,} rows (day types read: {', '.join(known) or 'none'})")
    if gone:
        logger.info(f"  {TABLE}: {len(gone)} version(s) no longer in force dropped")
    return full or changed
