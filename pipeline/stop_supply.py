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

Departures are timetable_sampling's. Times are service-day seconds
(pipeline.service_day): 00:40 written at face value is 24:40, the same
night's.

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
from pipeline.day_types import ServiceCalendar, fallback_day_type
from pipeline.service_day import service_day_offset
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
    """One stop's departures on one day, seconds from midnight as the feed
    wrote them, as HourCells by service-day hour. A time before the cutover
    is the same night's, a day later than its face value."""
    secs = sorted(service_day_offset(sec) for sec in departure_secs)
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
    """What decides agg_stop_supply_hourly's rows, for its registry fingerprint."""
    return [
        inspect.getsource(hour_cells),
        inspect.getsource(service_day_offset),
        *render_days(),
    ]


def render_days() -> list[str]:
    """What decides agg_stop_supply_days' rows, for its registry fingerprint.
    The shared module is rendered whole, its departures SQL included, so a
    change made there for the revision comparison moves these too."""
    return [
        inspect.getsource(timetable_sampling),
        inspect.getsource(versions_in_force),
        inspect.getsource(typical_days),
        inspect.getsource(ServiceCalendar),
        inspect.getsource(fallback_day_type),
    ]


Days = dict[SupplyDayType, TypicalDay | None]


def _desired(ch, agency_id: int) -> dict[tuple[str, str], Days]:
    """Each version in force with the typical day of each type it is read on."""
    join = static_archive.schedule_join(ch, agency_id, None, by_service_date=True)
    kept = static_archive.calendar_versions(ch, agency_id)
    out: dict[tuple[str, str], Days] = {}
    for version in versions_in_force(join.in_force()):
        key = (version.stem, version.content)
        archived = static_archive.archived_calendar(ch, agency_id, *key) if key in kept else None
        out[key] = (
            dict.fromkeys(DAY_TYPES) if archived is None else typical_days(version.segments, service_calendar(archived))
        )
    return out


def _stored(conn, agency_id: int) -> dict[tuple[str, str], dict[str, date | None]]:
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT static_version_id, content_hash, day_type, typical_day FROM {DAYS_TABLE} WHERE agency_id = %s",
            (agency_id,),
        )
        out: dict[tuple[str, str], dict[str, date | None]] = {}
        for stem, content, day_type, day in cur.fetchall():
            out.setdefault((stem, content), {})[day_type] = day
        return out


def _days_of(days: Days) -> dict[str, date | None]:
    return {day_type: None if typical is None else typical.day for day_type, typical in days.items()}


def hourly_rows(agency_id: int, stem: str, content: str, rows: Iterable[tuple]) -> list[tuple]:
    """timetable_sampling.DEPARTURE_SECONDS_SQL's rows, (day type index,
    stop_id, departure seconds), as
    agg_stop_supply_hourly rows of version *stem* at *content*."""
    out = []
    for index, stop_id, secs in rows:
        day_type = DAY_TYPES[index - 1]
        for hour, cell in sorted(hour_cells(secs).items()):
            out.append((agency_id, stem, content, day_type, stop_id, hour, *cell))
    return out


def _build(conn, ch, agency_id: int, stem: str, content: str, days: Days) -> int:
    services = timetable_sampling.services_by_day_type(days)
    rows: list[tuple] = []
    if any(services):
        parameters = {"agency": agency_id, "content": content, "services": services}
        result = ch.query(timetable_sampling.DEPARTURE_SECONDS_SQL, parameters=parameters)
        rows = hourly_rows(agency_id, stem, content, result.result_rows)
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(
            cur,
            f"INSERT INTO {DAYS_TABLE} (agency_id, static_version_id, content_hash, day_type, typical_day) VALUES %s",
            [(agency_id, stem, content, day_type, day) for day_type, day in _days_of(days).items()],
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


def rebuild(conn, ch, agency_id: int, *, full: bool) -> bool:
    """Bring the agency's rows to the versions in force now, in *conn*'s open
    transaction, without committing; whether any row changed.

    *full* rebuilds every version. Otherwise only a version not built yet, or
    whose typical day of some type moved because the dates it is in force on
    did, is built, and the rows of a version no longer in force go.
    """
    desired = _desired(ch, agency_id)
    stored: dict[tuple[str, str], dict[str, date | None]] = {}
    if full:
        with conn.cursor() as cur:
            for table in (DAYS_TABLE, TABLE):
                cur.execute(f"DELETE FROM {table} WHERE agency_id = %s", (agency_id,))
    else:
        stored = _stored(conn, agency_id)
    stale = [key for key, days in stored.items() if key not in desired or days != _days_of(desired[key])]
    _delete(conn, agency_id, stale)
    building = [key for key in desired if key not in stored or key in stale]
    for stem, content in building:
        count = _build(conn, ch, agency_id, stem, content, desired[(stem, content)])
        known = [day_type for day_type, typical in desired[(stem, content)].items() if typical is not None]
        logger.info(f"  {TABLE}: {stem} {count:,} rows (day types read: {', '.join(known) or 'none'})")
    gone = [key for key in stale if key not in desired]
    if gone:
        logger.info(f"  {TABLE}: {len(gone)} version(s) no longer in force dropped")
    return full or bool(building or gone)
