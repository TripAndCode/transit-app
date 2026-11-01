"""The delay record a passenger asks for: how late their trip left the stop
they boarded at, on one service day.

A passenger finds it in four steps -- the day, the route, the trip, the stop
-- and this module serves the last two. :func:`compute_record_trips` lists
every trip the route ran that day, and :func:`compute_record_stops` lists one
trip's stops in order with the departure delay each one recorded.

A trip that leaves its first stop on time can still leave a later stop late,
so the record is read at the chosen stop. The staff list in
:func:`pipeline.reports.council.compute_delay_certificate` keeps one row per
trip at its first recorded stop instead.

The trips offered are the ones observed that day, not the ones the loaded
timetable plans: only the loaded timetable is kept, so a day before it starts
has no plan at all, while the feed still recorded that day's trips. A trip
with no reading that day has no record to give, so it is not offered.

Both reads are a live ClickHouse scan over the shared dedup
(:func:`pipeline.db.build_dedup_ch_sql`: the latest plausible poll per stop
event), filtered to one agency, one service day, one route and, for the
stops, one trip before any aggregation, so every filter is a row-level
predicate ClickHouse can apply in PREWHERE. Names, the origin departure and
where the trip runs to come from the loaded timetable only when it runs the
trip that day (:func:`pipeline.planned_trips.running_trip_join`).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from api.clickhouse import table_holding
from api.range import RangeCtx, build_updates_filter_ch
from pipeline.db import build_dedup_ch_sql
from pipeline.planned_trips import plan_ctes, running_trip_join
from pipeline.route_terminals import normalize_stop_name
from pipeline.service_day import clock_to_sec, service_day_offset


def _record_filter(day: date, route_code: str, trip_id: str | None = None) -> tuple[str, dict]:
    """The row-level WHERE for one service day of one route, and one trip
    when given: the same date and route clauses every report scan uses."""
    where, params = build_updates_filter_ch(RangeCtx(from_date=day, to_date=day, routes=(route_code,)))
    if trip_id is not None:
        where += " AND u.trip_id = {record_trip:String}"
        params["record_trip"] = trip_id
    return where, params


def _stop_readings_cte(where: str, table: str) -> str:
    """``stops``: one row per ``(trip_id, stop_sequence)`` with the reading the
    dedup keeps for it, as ``(scheduled_time, scheduled_sec, dep_delay)``.

    The dedup's GROUP BY also holds ``scheduled_time``, ``route_code`` and
    ``service_type``, so a static reload between two polls of one stop splits
    it in two; the later capture is the one kept, as the dedup keeps the
    later poll within a group.
    """
    body = build_dedup_ch_sql(extra_where=where, include_captured_at=True, include_scheduled_sec=True, table=table)
    return (
        f"deduped AS ({body}),\n"
        "stops AS (\n"
        "    SELECT trip_id, stop_sequence,\n"
        "        argMax(tuple(scheduled_time, scheduled_sec, dep_delay), last_captured_at) AS reading\n"
        "    FROM deduped\n"
        "    GROUP BY trip_id, stop_sequence\n"
        ")"
    )


def record_trips_sql(where: str, table: str) -> str:
    """Each observed trip: how many stops it has a reading at, and the
    schedule of the earliest one as ``(scheduled_time, scheduled_sec)``."""
    return (
        f"WITH {_stop_readings_cte(where, table)}\n"
        "SELECT trip_id, count() AS observed_stops,\n"
        "    argMin(tuple(reading.1, reading.2), stop_sequence) AS first_schedule\n"
        "FROM stops\n"
        "GROUP BY trip_id"
    )


def record_stops_sql(where: str, table: str) -> str:
    """One trip's readings, by stop: ``(stop_sequence, scheduled_time,
    scheduled_sec, dep_delay)`` in stop order."""
    return (
        f"WITH {_stop_readings_cte(where, table)}\n"
        "SELECT stop_sequence, reading.1, reading.2, reading.3\n"
        "FROM stops\n"
        "ORDER BY stop_sequence"
    )


# The first stop's time, whichever of the two the feed fills in.
_STOP_TIME = "COALESCE(NULLIF(btrim(st.departure_time), ''), NULLIF(btrim(st.arrival_time), ''))"

# For each trip the loaded timetable runs on the day: its headsign, its first
# stop's departure and its last stop's name.
_TIMETABLE_TRIPS_SQL = (
    "WITH"
    + plan_ctes("ARRAY[$1::int]", "$2::date", "$2::date")
    + """
    SELECT q.trip_id, NULLIF(btrim(t.trip_headsign), '') AS headsign, o.departure AS origin_departure,
           e.stop_name AS terminal
    FROM unnest($3::text[]) AS q(trip_id)
    """
    + running_trip_join("$1", "q.trip_id", "$2::date")
    + f"""
    LEFT JOIN LATERAL (
        SELECT {_STOP_TIME} AS departure
        FROM static_stop_times st
        WHERE st.agency_id = $1 AND st.trip_id = q.trip_id
        ORDER BY st.stop_sequence
        LIMIT 1
    ) o ON TRUE
    LEFT JOIN LATERAL (
        SELECT NULLIF(btrim(s.stop_name), '') AS stop_name
        FROM static_stop_times st
        LEFT JOIN static_stops s ON s.agency_id = st.agency_id AND s.stop_id = st.stop_id
        WHERE st.agency_id = $1 AND st.trip_id = q.trip_id
        ORDER BY st.stop_sequence DESC
        LIMIT 1
    ) e ON TRUE
"""
)

# One trip's stops in the loaded timetable, when it runs the trip on the day.
_TIMETABLE_STOPS_SQL = (
    "WITH"
    + plan_ctes("ARRAY[$1::int]", "$2::date", "$2::date")
    + """
    SELECT st.stop_sequence, NULLIF(btrim(s.stop_name), '') AS stop_name, """
    + _STOP_TIME
    + """ AS departure
    FROM (SELECT $3::text AS trip_id) AS q
    """
    + running_trip_join("$1", "q.trip_id", "$2::date")
    + """
    JOIN static_stop_times st ON st.agency_id = $1 AND st.trip_id = q.trip_id
    LEFT JOIN static_stops s ON s.agency_id = st.agency_id AND s.stop_id = st.stop_id
    ORDER BY st.stop_sequence
"""
)


def _reading_time(scheduled_time: str | None, scheduled_sec: int | None) -> int | None:
    """The time a reading carries, in service-day seconds: the value ingest
    stored, or its clock when that is missing. Only labels a trip the
    timetable does not run; the clock is the trip's start for an
    aomori_regex agency (see :func:`record_stops`), never a stop's schedule."""
    return service_day_offset(int(scheduled_sec)) if scheduled_sec is not None else clock_to_sec(scheduled_time)


def record_trips(
    observed: Sequence[tuple[str, int, tuple[str | None, int | None]]],
    timetable: Mapping[str, Mapping[str, Any]],
) -> list[dict]:
    """Step 3's trips, ordered by departure.

    ``observed`` is :func:`record_trips_sql`'s rows; ``timetable`` maps each
    trip the loaded timetable runs on the day to its ``headsign``,
    ``origin_departure`` and ``terminal``.

    A trip the timetable runs is labelled by its origin departure and where
    it runs to, its last stop. That stop's name drops platform and note
    annotations (:func:`pipeline.route_terminals.normalize_stop_name`), as a
    terminal's alighting platform is not where the trip goes. Feeds fill
    ``trip_headsign`` with a via point, a line name or a route code as often
    as a destination, so it is kept beside the terminal, not in its place,
    and dropped when, notes aside, it only repeats it.

    Any other trip is labelled by the time its earliest reading carries,
    with neither (``matched`` False): that stop need not be where the trip
    began, and nothing else says where it ran to.
    """
    trips = []
    for trip_id, observed_stops, (first_time, first_sec) in observed:
        plan = timetable.get(trip_id)
        terminal = headsign = None
        if plan is not None:
            departure_sec = clock_to_sec(plan["origin_departure"])
            terminal = normalize_stop_name(plan["terminal"])
            headsign = plan["headsign"] if normalize_stop_name(plan["headsign"]) != terminal else None
        else:
            departure_sec = _reading_time(first_time, first_sec)
        trips.append(
            {
                "trip_id": trip_id,
                "departure_sec": departure_sec,
                "terminal": terminal,
                "headsign": headsign,
                "matched": plan is not None,
                "observed_stops": int(observed_stops),
            }
        )
    trips.sort(key=lambda t: (t["departure_sec"] is None, t["departure_sec"] or 0, t["trip_id"]))
    return trips


def record_stops(
    observed: Sequence[tuple[int, str | None, int | None, int]],
    timetable: Sequence[tuple[int, str | None, str | None]],
) -> list[dict]:
    """Step 4's stops in stop order, each with its departure reading or none.

    ``observed`` is :func:`record_stops_sql`'s rows; ``timetable`` is the
    loaded timetable's ``(stop_sequence, stop_name, departure)`` for the trip,
    empty when it does not run the trip that day. Every timetable stop is
    listed, and so is a recorded stop it does not hold, unnamed.

    A stop's schedule is the reading's ``scheduled_sec`` when it has one:
    the static join stores it per stop at ingest, and the delay was measured
    against it, so the actual departure is that schedule plus the delay.
    Otherwise it is the timetable's departure there. Both go through the
    service-day cutover (:func:`pipeline.service_day.service_day_offset`,
    :func:`~pipeline.service_day.clock_to_sec`): ingest stores a feed's
    un-extended ``00:10`` as written, which would otherwise sit a day before
    a timetable neighbour. A reading's
    ``scheduled_time`` clock is never used: the aomori_regex ingest writes
    the trip's start time, read from its trip_id, as the clock of every stop
    and stores no ``scheduled_sec``.
    """
    stops: dict[int, dict] = {}
    for stop_sequence, stop_name, departure in timetable:
        stops[int(stop_sequence)] = {
            "stop_sequence": int(stop_sequence),
            "stop_name": stop_name,
            "scheduled_sec": clock_to_sec(departure),
            "dep_delay": None,
        }
    for stop_sequence, _clock, scheduled_sec, dep_delay in observed:
        stop = stops.setdefault(
            int(stop_sequence),
            {"stop_sequence": int(stop_sequence), "stop_name": None, "scheduled_sec": None, "dep_delay": None},
        )
        if scheduled_sec is not None:
            stop["scheduled_sec"] = service_day_offset(int(scheduled_sec))
        stop["dep_delay"] = int(dep_delay)
    return [stops[k] for k in sorted(stops)]


async def _timetable_trips(conn, agency_id: int, day: date, trip_ids: list[str]) -> dict[str, Mapping[str, Any]]:
    rows = await conn.fetch(_TIMETABLE_TRIPS_SQL, agency_id, day, trip_ids)
    return {r["trip_id"]: r for r in rows}


async def compute_record_trips(agency_id: int, day: date, route_code: str, conn, ch) -> list[dict]:
    """Every trip ``route_code`` ran on service day ``day`` with a departure
    reading, as :func:`record_trips` labels them. Empty when the feed
    recorded no trip of the route that day."""
    if ch is None:
        raise RuntimeError("compute_record_trips requires a ClickHouse client")
    where, params = _record_filter(day, route_code)
    table = await table_holding(ch, agency_id, day)
    result = await ch.query(record_trips_sql(where, table), parameters={"agency_id": agency_id, **params})
    observed = result.result_rows
    if not observed:
        return []
    timetable = await _timetable_trips(conn, agency_id, day, [row[0] for row in observed])
    return record_trips(observed, timetable)


async def compute_record_stops(agency_id: int, day: date, route_code: str, trip_id: str, conn, ch) -> dict:
    """One trip's stops on service day ``day``, as :func:`record_stops` lists
    them, with the trip as :func:`record_trips` labels it.

    Returns ``{"trip", "stops"}``; both are empty (``None``, ``[]``) when the
    trip has no reading that day, since there is no record to give.
    """
    if ch is None:
        raise RuntimeError("compute_record_stops requires a ClickHouse client")
    where, params = _record_filter(day, route_code, trip_id)
    table = await table_holding(ch, agency_id, day)
    result = await ch.query(record_stops_sql(where, table), parameters={"agency_id": agency_id, **params})
    observed = result.result_rows
    if not observed:
        return {"trip": None, "stops": []}
    timetable = await _timetable_trips(conn, agency_id, day, [trip_id])
    plan_stops = await conn.fetch(_TIMETABLE_STOPS_SQL, agency_id, day, trip_id) if timetable else []
    _, first_time, first_sec, _ = observed[0]
    (trip,) = record_trips([(trip_id, len(observed), (first_time, first_sec))], timetable)
    stops = record_stops(observed, [(r["stop_sequence"], r["stop_name"], r["departure"]) for r in plan_stops])
    return {"trip": trip, "stops": stops}
