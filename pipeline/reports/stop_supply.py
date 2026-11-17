"""Every operator's stops on one map: the planned supply of each over a band
of hours, from the timetable version in force on a service date, read on that
version's typical day of a day type (pipeline.stop_supply).

Each operator's stops stand alone: two operators' stops at one place are two
stops, their departures not added together, since nothing yet tells which
stops of different operators are one place.
"""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import NamedTuple

from pipeline import static_archive
from pipeline.cache import async_lru_cache
from pipeline.stop_supply import DAY_TYPES, BandSupply, HourCell, SupplyDayType, band_supply

# The service-day hours a band may span: from the 04:00 cutover to the next
# one, whose after-midnight hours are 24 to 27.
FIRST_HOUR = 4
LAST_HOUR = 28


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


class AgencySupply(NamedTuple):
    agency_id: int
    agency_name: str
    status: AgencyStatus
    static_version_id: str | None
    typical_day: date | None


class StopSupply(NamedTuple):
    agency_id: int
    stop_id: str
    stop_name: str | None
    lat: float | None
    lon: float | None
    band: BandSupply
    # The day's first and last departure in service-day seconds, None for a
    # stop of the version with no departure on this day type.
    first_sec: int | None
    last_sec: int | None


class SupplyMap(NamedTuple):
    agencies: list[AgencySupply]
    stops: list[StopSupply]


@async_lru_cache(maxsize=64, ttl_seconds=300, agency_arg="agency_id")
async def _in_force(ch, agency_id: int) -> list[tuple[date, date, str, str]]:
    join = await static_archive.schedule_join_async(ch, agency_id, None, by_service_date=True)
    return join.in_force()


def version_on(segments: list[tuple[date, date, str, str]], day: date) -> tuple[str, str] | None:
    """The (version name, content) in force on *day* among *segments*, or None."""
    return next(((stem, content) for first, last, stem, content in segments if first <= day <= last), None)


_DAYS_SQL = """
    SELECT c.agency_id, c.stem, d.typical_day, d.day_type IS NOT NULL AS built
    FROM unnest($1::int[], $2::text[], $3::text[]) AS c(agency_id, stem, content)
    LEFT JOIN agg_stop_supply_days d
      ON d.agency_id = c.agency_id AND d.static_version_id = c.stem AND d.content_hash = c.content
     AND d.day_type = $4
"""

# Every stop with a departure on any day type of the chosen version, so a
# stop with none on this day type reads as zero rather than vanishing; its
# position and name from the loaded timetable, absent for a stop it no longer
# has. The band's hours come as (hour, departures, first_sec, last_sec,
# max_gap_sec) rows.
_STOPS_SQL = """
    WITH chosen AS (
        SELECT * FROM unnest($1::int[], $2::text[], $3::text[]) AS c(agency_id, stem, content)
    ),
    version_stops AS (
        SELECT DISTINCT h.agency_id, h.static_version_id, h.content_hash, h.stop_id
        FROM agg_stop_supply_hourly h
        JOIN chosen c ON c.agency_id = h.agency_id AND c.stem = h.static_version_id AND c.content = h.content_hash
    )
    SELECT v.agency_id, v.stop_id, s.stop_name, s.stop_lat, s.stop_lon,
           MIN(h.first_sec) AS first_sec, MAX(h.last_sec) AS last_sec,
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
    GROUP BY v.agency_id, v.stop_id, s.stop_name, s.stop_lat, s.stop_lon
    ORDER BY v.agency_id, v.stop_id
"""


def _cells(band: list[list[int | None]]) -> dict[int, HourCell]:
    return {
        int(hour): HourCell(int(departures), int(first), int(last), None if gap is None else int(gap))
        for hour, departures, first, last, gap in band
        if hour is not None and departures is not None and first is not None and last is not None
    }


async def read_supply_map(conn, ch, day: date, day_type: SupplyDayType, from_hour: int, to_hour: int) -> SupplyMap:
    """Each active operator's status and every stop of those that are OK."""
    if day_type not in DAY_TYPES:
        raise ValueError(f"unknown day type {day_type!r}")
    if not FIRST_HOUR <= from_hour < to_hour <= LAST_HOUR:
        raise ValueError(f"band [{from_hour}, {to_hour}) outside [{FIRST_HOUR}, {LAST_HOUR})")
    agencies = await conn.fetch(
        "SELECT agency_id, agency_name FROM agencies WHERE deleted_at IS NULL ORDER BY agency_id"
    )
    chosen: dict[int, tuple[str, str]] = {}
    for row in agencies:
        version = version_on(await _in_force(ch, row["agency_id"]), day)
        if version is not None:
            chosen[row["agency_id"]] = version
    ids = list(chosen)
    stems = [chosen[a][0] for a in ids]
    contents = [chosen[a][1] for a in ids]
    days = {r["agency_id"]: r for r in await conn.fetch(_DAYS_SQL, ids, stems, contents, day_type)}
    out: list[AgencySupply] = []
    readable: list[int] = []
    for row in agencies:
        agency_id = row["agency_id"]
        name = row["agency_name"]
        if agency_id not in chosen:
            out.append(AgencySupply(agency_id, name, AgencyStatus.NO_TIMETABLE, None, None))
            continue
        stem = chosen[agency_id][0]
        built = days.get(agency_id)
        if built is None or not built["built"]:
            out.append(AgencySupply(agency_id, name, AgencyStatus.NOT_BUILT, stem, None))
        elif built["typical_day"] is None:
            out.append(AgencySupply(agency_id, name, AgencyStatus.UNKNOWN_DAY, stem, None))
        else:
            out.append(AgencySupply(agency_id, name, AgencyStatus.OK, stem, built["typical_day"]))
            readable.append(agency_id)
    rows = await conn.fetch(
        _STOPS_SQL,
        readable,
        [chosen[a][0] for a in readable],
        [chosen[a][1] for a in readable],
        day_type,
        from_hour,
        to_hour,
    )
    stops = [
        StopSupply(
            r["agency_id"],
            r["stop_id"],
            r["stop_name"],
            r["stop_lat"],
            r["stop_lon"],
            band_supply(_cells(r["band"]), from_hour, to_hour),
            r["first_sec"],
            r["last_sec"],
        )
        for r in rows
    ]
    return SupplyMap(out, stops)
