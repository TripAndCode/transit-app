"""Every operator's stops on one map: the planned supply of each over a band
of hours, from the timetable version in force on a service date, read on that
version's typical day of a day type (pipeline.stop_supply).

Each operator's stops stand alone: two operators' stops at one place are two
stops, their departures not added together, since nothing yet tells which
stops of different operators are one place.

The map carries every stop of every operator, so it sends each operator's
stops as columns (one array per field, no repeated keys) with only what the
map draws; a stop's name and its first and last departure come from the stop
read (read_supply_stop) when one is opened, with each hour's observed
departures beside its planned ones.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum
from typing import Any, NamedTuple

from pipeline import static_archive
from pipeline.cache import async_lru_cache
from pipeline.day_types import fallback_day_type, public_holidays_between
from pipeline.histogram import on_time_from_columns
from pipeline.reports.rankings import DEFAULT_EARLY_TOLERANCE_SEC, DEFAULT_LATE_TOLERANCE_SEC, _pct1
from pipeline.service_day import SERVICE_DAY_CUTOVER_HOURS
from pipeline.stats import pct_is_uncertain
from pipeline.stop_supply import DAY_TYPES, HourCell, SupplyDayType, band_supply

# The service-day hours a band may span: from the cutover to the next one,
# whose after-midnight hours are 24 and later.
FIRST_HOUR = SERVICE_DAY_CUTOVER_HOURS
LAST_HOUR = FIRST_HOUR + 24
# Five decimals of a degree are about a metre, finer than a stop pole.
_COORD_DECIMALS = 5
# An opened stop's observed departures are pooled over the service days, among
# the last OBSERVED_DAYS up to the newest one observed, that ran the same
# timetable version on the same day type.
OBSERVED_DAYS = 30


class AgencyStatus(str, Enum):
    """Whether an operator's stops can be read for the date and day type."""

    OK = "ok"
    # No version of its timetable kept in the archive is in force on the date.
    NO_TIMETABLE = "no_timetable"
    # A version is in force, but analyze has not built its supply yet.
    NOT_BUILT = "not_built"
    # The version cannot tell its typical day of the type (no such date in
    # force within its calendar, or no calendar kept): unknown, never zero.
    UNKNOWN_DAY = "unknown_day"


@async_lru_cache(maxsize=64, ttl_seconds=300, agency_arg="agency_id")
async def _in_force(ch, agency_id: int) -> list[tuple[date, date, str, str]]:
    join = await static_archive.schedule_join_async(ch, agency_id, None, by_service_date=True)
    return join.in_force()


def version_on(segments: list[tuple[date, date, str, str]], day: date) -> tuple[str, str] | None:
    """The (version name, content) in force on *day* among *segments*, or None."""
    return next(((stem, content) for first, last, stem, content in segments if first <= day <= last), None)


def _check(day_type: str, from_hour: int, to_hour: int) -> None:
    if day_type not in DAY_TYPES:
        raise ValueError(f"unknown day type {day_type!r}")
    if not FIRST_HOUR <= from_hour < to_hour <= LAST_HOUR:
        raise ValueError(f"band [{from_hour}, {to_hour}) outside [{FIRST_HOUR}, {LAST_HOUR})")


_DAYS_SQL = """
    SELECT c.agency_id, d.typical_day, d.day_type IS NOT NULL AS built
    FROM unnest($1::int[], $2::text[], $3::text[]) AS c(agency_id, stem, content)
    LEFT JOIN agg_stop_supply_days d
      ON d.agency_id = c.agency_id AND d.static_version_id = c.stem AND d.content_hash = c.content
     AND d.day_type = $4
"""


async def _statuses(conn, ch, day: date, day_type: str) -> tuple[list[dict[str, Any]], dict[int, tuple[str, str]]]:
    """Each active operator's status for *day* and *day_type*, and the version
    of each that can be read."""
    agencies = await conn.fetch(
        "SELECT agency_id, agency_name FROM agencies WHERE deleted_at IS NULL ORDER BY agency_id"
    )
    segments = await asyncio.gather(*(_in_force(ch, row["agency_id"]) for row in agencies))
    chosen = {
        row["agency_id"]: version
        for row, found in zip(agencies, segments, strict=True)
        if (version := version_on(found, day)) is not None
    }
    ids = list(chosen)
    days = {
        r["agency_id"]: r
        for r in await conn.fetch(_DAYS_SQL, ids, [chosen[a][0] for a in ids], [chosen[a][1] for a in ids], day_type)
    }
    out: list[dict[str, Any]] = []
    readable: dict[int, tuple[str, str]] = {}
    for row in agencies:
        agency_id = row["agency_id"]
        version = chosen.get(agency_id)
        built = days.get(agency_id)
        typical_day = None
        if version is None:
            status = AgencyStatus.NO_TIMETABLE
        elif built is None or not built["built"]:
            status = AgencyStatus.NOT_BUILT
        elif built["typical_day"] is None:
            status = AgencyStatus.UNKNOWN_DAY
        else:
            status = AgencyStatus.OK
            typical_day = built["typical_day"].isoformat()
            readable[agency_id] = version
        out.append(
            {
                "agency_id": agency_id,
                "agency_name": row["agency_name"],
                "status": status.value,
                "static_version_id": None if version is None else version[0],
                "typical_day": typical_day,
            }
        )
    return out, readable


# Every stop with a departure on any day type of the chosen version, so a
# stop with none on this day type reads as zero rather than vanishing; its
# position from the loaded timetable, absent for a stop it no longer has. The
# band's hours come as (hour, departures, first_sec, last_sec, max_gap_sec).
_STOPS_SQL = """
    WITH chosen AS (
        SELECT * FROM unnest($1::int[], $2::text[], $3::text[]) AS c(agency_id, stem, content)
    ),
    version_stops AS (
        SELECT DISTINCT h.agency_id, h.static_version_id, h.content_hash, h.stop_id
        FROM agg_stop_supply_hourly h
        JOIN chosen c ON c.agency_id = h.agency_id AND c.stem = h.static_version_id AND c.content = h.content_hash
    )
    SELECT v.agency_id, v.stop_id, s.stop_lat, s.stop_lon,
           COALESCE(
               array_agg(ARRAY[h.hour, h.departures, h.first_sec, h.last_sec, h.max_gap_sec] ORDER BY h.hour)
                   FILTER (WHERE h.hour >= $5 AND h.hour < $6),
               '{}'
           ) AS band
    FROM version_stops v
    LEFT JOIN agg_stop_supply_hourly h
      ON h.agency_id = v.agency_id AND h.static_version_id = v.static_version_id
     AND h.content_hash = v.content_hash AND h.day_type = $4 AND h.stop_id = v.stop_id
    LEFT JOIN static_stops s ON s.agency_id = v.agency_id AND s.stop_id = v.stop_id
    GROUP BY v.agency_id, v.stop_id, s.stop_lat, s.stop_lon
    ORDER BY v.agency_id, v.stop_id
"""


def _cells(hours: list[list[Any]]) -> dict[int, HourCell]:
    """_STOPS_SQL's band as HourCells. Only max_gap_sec may be NULL: the rest
    are NOT NULL columns of a matched row, and FILTER leaves out the unmatched."""
    return {hour: HourCell(departures, first, last, gap) for hour, departures, first, last, gap in hours}


def _coord(value: float | None) -> float | None:
    return None if value is None else round(value, _COORD_DECIMALS)


@async_lru_cache(maxsize=32, ttl_seconds=300)
async def read_supply_map(conn, ch, day: date, day_type: SupplyDayType, from_hour: int, to_hour: int) -> dict:
    """The map's payload: each active operator's status, and the stops of
    those that are OK as columns of stop_id, lat, lon, departures and
    longest_gap_min over [*from_hour*, *to_hour*). Cached like the reports and
    dropped by every analyze rebuild; the result is shared by every caller and
    must not be modified."""
    _check(day_type, from_hour, to_hour)
    agencies, readable = await _statuses(conn, ch, day, day_type)
    ids = list(readable)
    rows = await conn.fetch(
        _STOPS_SQL,
        ids,
        [readable[a][0] for a in ids],
        [readable[a][1] for a in ids],
        day_type,
        from_hour,
        to_hour,
    )
    columns: dict[int, dict[str, list]] = {
        a: {"stop_id": [], "lat": [], "lon": [], "departures": [], "longest_gap_min": []} for a in ids
    }
    for r in rows:
        supply = band_supply(_cells(r["band"]), from_hour, to_hour)
        stops = columns[r["agency_id"]]
        stops["stop_id"].append(r["stop_id"])
        stops["lat"].append(_coord(r["stop_lat"]))
        stops["lon"].append(_coord(r["stop_lon"]))
        stops["departures"].append(supply.departures)
        stops["longest_gap_min"].append(round(supply.longest_gap_sec / 60, 1))
    for agency in agencies:
        agency["stops"] = columns.get(agency["agency_id"])
    return {
        "date": day.isoformat(),
        "day_type": day_type,
        "from_hour": from_hour,
        "to_hour": to_hour,
        "agencies": agencies,
    }


_STOP_SQL = """
    SELECT h.hour, h.departures, h.first_sec, h.last_sec, h.max_gap_sec
    FROM agg_stop_supply_hourly h
    WHERE h.agency_id = $1 AND h.static_version_id = $2 AND h.content_hash = $3 AND h.day_type = $4
      AND h.stop_id = $5
"""
_STOP_KNOWN_SQL = """
    SELECT (SELECT stop_name FROM static_stops WHERE agency_id = $1 AND stop_id = $5) AS stop_name,
           EXISTS (
               SELECT 1 FROM agg_stop_supply_hourly
               WHERE agency_id = $1 AND static_version_id = $2 AND content_hash = $3 AND stop_id = $5
           ) AS known,
           (SELECT typical_day FROM agg_stop_supply_days
            WHERE agency_id = $1 AND static_version_id = $2 AND content_hash = $3 AND day_type = $4) AS typical_day
"""


# The service days the agency was observed on: a day the feed was silent has
# no row, so it neither counts as a day nor reads as one with no departure.
_OBSERVED_DATES_SQL = """
    WITH newest AS (SELECT max(date) AS d FROM agg_hour_daily WHERE agency_id = $1 AND date <= $2)
    SELECT DISTINCT h.date
    FROM agg_hour_daily h, newest
    WHERE h.agency_id = $1 AND h.date > newest.d - $3::int AND h.date <= newest.d
    ORDER BY h.date
"""
_OBSERVED_SQL = """
    SELECT hour, SUM(samples)::int, SUM(sum_delay_sec)::bigint, SUM(on_time_count)::int,
           SUM(late5_count)::int, SUM(early_count)::int
    FROM agg_stop_hour_daily
    WHERE agency_id = $1 AND stop_id = $2 AND date = ANY($3::date[]) AND hour >= $4 AND hour < $5
    GROUP BY hour
"""


class ObservedHour(NamedTuple):
    """One stop's observed departures in one service-day hour, pooled over
    the window's days: agg_stop_hour_daily's columns summed."""

    samples: int
    sum_delay_sec: int
    on_time_count: int
    late5_count: int
    early_count: int


def matching_days(
    dates: Iterable[date], segments: list[tuple[date, date, str, str]], version: tuple[str, str], day_type: str
) -> list[date]:
    """The *dates* on which *version* was in force and which are *day_type*
    by the weekday rule, the rule the typical day was chosen by
    (pipeline.timetable_sampling)."""
    dates = sorted(dates)
    if not dates:
        return []
    holidays = set(public_holidays_between(dates[0], dates[-1]))
    return [d for d in dates if fallback_day_type(d, holidays) == day_type and version_on(segments, d) == version]


def _tenth(numerator: int, denominator: int) -> Decimal:
    """*numerator* / *denominator* to one decimal, half-up, exactly."""
    return (Decimal(numerator) / denominator).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def hour_rows(
    planned: Mapping[int, HourCell],
    observed: Mapping[int, ObservedHour] | None,
    days: int,
    from_hour: int,
    to_hour: int,
) -> list[dict[str, Any]]:
    """Each hour of [*from_hour*, *to_hour*) with a planned or an observed
    departure: its planned departures on the typical day and, with an
    observed window of *days* days, what was observed. On time is the
    default window's, of the departures observed: one not observed is not
    counted late, since a feed that missed it says nothing of its running.
    The percentage comes however few departures back it, flagged uncertain
    when its 95% interval is wider than five points either way."""
    rows = []
    for hour in range(from_hour, to_hour):
        cell = planned.get(hour)
        seen = None if observed is None else observed.get(hour)
        if cell is None and seen is None:
            continue
        row: dict[str, Any] = {"hour": hour, "departures": 0 if cell is None else cell.departures, "observed": None}
        if observed is not None:
            seen = seen or ObservedHour(0, 0, 0, 0, 0)
            n = seen.samples
            on_time = on_time_from_columns(
                DEFAULT_EARLY_TOLERANCE_SEC,
                DEFAULT_LATE_TOLERANCE_SEC,
                samples=n,
                on_time_count=seen.on_time_count,
                late5_count=seen.late5_count,
                early_count=seen.early_count,
            )
            row["observed"] = {
                "departures": n,
                "per_day": _tenth(n, days),
                "on_time": on_time,
                "on_time_pct": _pct1(on_time, n) if n else None,
                "uncertain": pct_is_uncertain(on_time, n),
                "avg_delay_min": _tenth(seen.sum_delay_sec, 60 * n) if n else None,
            }
        rows.append(row)
    return rows


async def _observed(
    conn, agency_id: int, stop_id: str, day: date, segments, version: tuple[str, str], day_type: str, hours: range
) -> tuple[list[date], dict[int, ObservedHour]] | None:
    """The days an opened stop's observed departures are pooled over and the
    pooled departures of each hour of *hours*, or None with no such day."""
    dates = [r[0] for r in await conn.fetch(_OBSERVED_DATES_SQL, agency_id, day, OBSERVED_DAYS)]
    days = matching_days(dates, segments, version, day_type)
    if not days:
        return None
    rows = await conn.fetch(_OBSERVED_SQL, agency_id, stop_id, days, hours.start, hours.stop)
    return days, {r[0]: ObservedHour(*r[1:]) for r in rows}


async def read_supply_stop(
    conn, ch, agency_id: int, stop_id: str, day: date, day_type: SupplyDayType, from_hour: int, to_hour: int
) -> dict | None:
    """One stop of the map, opened: its name, its supply over the band, the
    day's first and last departure in service-day seconds (None with no
    departure on the day type), and each hour of the band set beside what was
    observed (hour_rows) over the window's days, None with no observed day.
    None when the operator is not an active one, as the map lists only those,
    or its version in force on *day* has no such stop or cannot be read for
    *day_type*."""
    _check(day_type, from_hour, to_hour)
    if not await conn.fetchval(
        "SELECT EXISTS (SELECT 1 FROM agencies WHERE agency_id = $1 AND deleted_at IS NULL)", agency_id
    ):
        return None
    segments = await _in_force(ch, agency_id)
    version = version_on(segments, day)
    if version is None:
        return None
    args = (agency_id, *version, day_type, stop_id)
    known = await conn.fetchrow(_STOP_KNOWN_SQL, *args)
    if not known["known"] or known["typical_day"] is None:
        return None
    hours = await conn.fetch(_STOP_SQL, *args)
    cells = {r["hour"]: HourCell(r["departures"], r["first_sec"], r["last_sec"], r["max_gap_sec"]) for r in hours}
    supply = band_supply(cells, from_hour, to_hour)
    observed = await _observed(conn, agency_id, stop_id, day, segments, version, day_type, range(from_hour, to_hour))
    days, by_hour = (None, None) if observed is None else observed
    return {
        "agency_id": agency_id,
        "stop_id": stop_id,
        "stop_name": known["stop_name"],
        "departures": supply.departures,
        "per_hour": round(supply.per_hour, 2),
        "longest_gap_min": round(supply.longest_gap_sec / 60, 1),
        "first_sec": min((c.first_sec for c in cells.values()), default=None),
        "last_sec": max((c.last_sec for c in cells.values()), default=None),
        "hours": hour_rows(cells, by_hour, 0 if days is None else len(days), from_hour, to_hour),
        "observed_window": None if days is None else {"first": days[0], "last": days[-1], "days": len(days)},
    }
