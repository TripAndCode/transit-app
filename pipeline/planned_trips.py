"""Planned trips per service day: as the loaded timetable plans them, and as saved.

A service runs on a date under the GTFS rule :mod:`pipeline.day_types` also
applies: its ``static_calendar`` (``calendar.txt``) range covers the date with
that weekday's flag set and no ``static_calendar_dates`` removal
(``exception_type = 2``) names the date, or a ``static_calendar_dates``
addition (``exception_type = 1``) names it. A service both cases put on the
same date runs once. Each running (service, date) plans the number of
``static_trips`` rows under that service.

The loaded timetable covers only the days its calendar says anything about,
and a reload (:mod:`pipeline.static_loader`) replaces an agency's calendar, so
every day before the new calendar starts loses its plan when the new one
lands. ``planned_trips_daily`` keeps each day's count as the loaded timetable
planned it when it was captured, with the stop events and the routes of those
trips. The capture inserts only days with no row, so the first count saved for
a day stands; the only later write is the one-time fill of a NULL
planned_stop_events (scripts/fill_planned_stop_events.py).

:func:`capture_planned_trips` runs on every analyze run and saves the days
before today. The day in progress is read from the loaded timetable until it
is over, so it follows the latest timetable loaded that day: a reload later in
the day may bring the plan it runs on. :func:`capture_before_reload` runs
before a reload replaces anything and saves the outgoing timetable's days
before the incoming one starts, today and the days after it included, since
nothing else will plan them once the outgoing calendar is gone. A timetable
that takes effect on the day it is loaded therefore plans that day itself, and
the next day's analyze saves it. Days before the first capture are not
reconstructed: the reload that replaced their timetable erased them.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Sequence
from datetime import date, datetime, timedelta, timezone
from typing import NamedTuple

from api.range import MAX_RANGE_DAYS
from pipeline.route_catalog import route_code_sql
from pipeline.service_day import service_date_of

logger = logging.getLogger(__name__)

#: How far a capture reaches: back from today, the longest report window, and
#: ahead of it, for a reload, as far again. Each capture recomputes its whole
#: window and inserts only the days with no row yet; a day further back is
#: saved only if an earlier capture reached it.
CAPTURE_LOOKBACK_DAYS = MAX_RANGE_DAYS

#: calendar.txt's weekly flags, Monday first (the order of ``date.weekday()``),
#: as ``static_calendar`` names its columns.
WEEKDAY_COLUMNS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


class PlanSource(NamedTuple):
    """The relations a plan reads, each named by a table or a CTE of the same
    query with the table's columns: the loaded timetable's tables, or another
    timetable's rows in their shape (:func:`rows_plan`), so every timetable is
    planned by the one rule."""

    trips: str = "static_trips"
    stop_times: str = "static_stop_times"
    calendar: str = "static_calendar"
    calendar_dates: str = "static_calendar_dates"


LOADED = PlanSource()


def plan_ctes(agency_ids: str, from_date: str, to_date: str, source: PlanSource = LOADED) -> str:
    """The CTEs that end in ``timetable_plan(agency_id, day, planned)``: one row
    per day of ``[from_date, to_date]`` the loaded timetable covers, with the
    trips it plans that day, 0 on a covered day nothing runs.

    The arguments are SQL expressions, so the asyncpg read and the psycopg2
    capture share one counting rule under their own placeholder styles:
    ``agency_ids`` an ``int[]``, the other two ``date`` values.

    A day is covered when it falls inside a ``calendar.txt`` service's range
    or a ``calendar_dates.txt`` row names it. ``static_calendar_dates.date`` is
    the feed's raw YYYYMMDD text and may hold a malformed row, which a DATE
    cast would fail on for every agency. The fixed-width text compares in
    calendar order, so BETWEEN bounds it, and matching it to the generated
    series' text drops a malformed row the lexicographic bounds would let
    through. Trips are counted once per service and multiplied by its running
    days, never joined row-per-trip-per-day.
    """
    return f"""
    days AS (
        SELECT day, to_char(day, 'YYYYMMDD') AS ymd, EXTRACT(ISODOW FROM day)::int AS isodow
        FROM (SELECT {from_date} + i AS day FROM generate_series(0, {to_date} - {from_date}) AS i) AS d
    ),
    trip_counts AS (
        SELECT agency_id, service_id, COUNT(*) AS trips
        FROM {source.trips}
        WHERE agency_id = ANY({agency_ids})
        GROUP BY agency_id, service_id
    ),
    running AS (
        SELECT c.agency_id, c.service_id, days.day
        FROM {source.calendar} c
        JOIN days ON days.day BETWEEN c.start_date AND c.end_date
        WHERE c.agency_id = ANY({agency_ids})
          AND (ARRAY[c.monday, c.tuesday, c.wednesday, c.thursday, c.friday, c.saturday, c.sunday])[days.isodow]
          AND NOT EXISTS (
              SELECT 1
              FROM {source.calendar_dates} x
              WHERE x.agency_id = c.agency_id
                AND x.service_id = c.service_id
                AND x.date = days.ymd
                AND x.exception_type = 2
          )
        UNION
        SELECT cd.agency_id, cd.service_id, days.day
        FROM {source.calendar_dates} cd
        JOIN days ON days.ymd = cd.date
        WHERE cd.agency_id = ANY({agency_ids})
          AND cd.exception_type = 1
          AND cd.date BETWEEN to_char({from_date}, 'YYYYMMDD') AND to_char({to_date}, 'YYYYMMDD')
    ),
    covered AS (
        SELECT c.agency_id, days.day
        FROM {source.calendar} c
        JOIN days ON days.day BETWEEN c.start_date AND c.end_date
        WHERE c.agency_id = ANY({agency_ids})
        UNION
        SELECT cd.agency_id, days.day
        FROM {source.calendar_dates} cd
        JOIN days ON days.ymd = cd.date
        WHERE cd.agency_id = ANY({agency_ids})
          AND cd.date BETWEEN to_char({from_date}, 'YYYYMMDD') AND to_char({to_date}, 'YYYYMMDD')
    ),
    timetable_plan AS (
        SELECT cv.agency_id, cv.day, COALESCE(SUM(t.trips), 0)::int AS planned
        FROM covered cv
        LEFT JOIN running r ON r.agency_id = cv.agency_id AND r.day = cv.day
        LEFT JOIN trip_counts t ON t.agency_id = r.agency_id AND t.service_id = r.service_id
        GROUP BY cv.agency_id, cv.day
    )"""


def stop_plan_ctes(agency_ids: str, source: PlanSource = LOADED) -> str:
    """The CTEs, after :func:`plan_ctes` in the same WITH, that end in
    ``timetable_stop_plan(agency_id, day, planned_stop_events)``: for each day
    ``timetable_plan`` has, the stop events its running trips plan, one per
    ``static_stop_times`` row, 0 on a covered day nothing runs. Counted once
    per service and multiplied by its running days, as trips are. Kept apart
    from ``timetable_plan`` because only the capture and the feed-quality
    scorecard read stop times; the board's plan never does."""
    return f""",
    service_stop_events AS (
        SELECT t.agency_id, t.service_id, COUNT(*) AS stop_events
        FROM {source.trips} t
        JOIN {source.stop_times} st ON st.agency_id = t.agency_id AND st.trip_id = t.trip_id
        WHERE t.agency_id = ANY({agency_ids})
        GROUP BY t.agency_id, t.service_id
    ),
    timetable_stop_plan AS (
        SELECT cv.agency_id, cv.day, COALESCE(SUM(s.stop_events), 0)::bigint AS planned_stop_events
        FROM covered cv
        LEFT JOIN running r ON r.agency_id = cv.agency_id AND r.day = cv.day
        LEFT JOIN service_stop_events s ON s.agency_id = r.agency_id AND s.service_id = r.service_id
        GROUP BY cv.agency_id, cv.day
    )"""


def route_plan_ctes(agency_ids: str) -> str:
    """The CTEs, after :func:`plan_ctes` in the same WITH, that end in
    ``timetable_route_plan(agency_id, day, by_route)``: for each day
    ``timetable_plan`` has, its planned trips by route as a JSONB object of
    route_code to trips, ``{}`` on a covered day nothing runs. A route is
    keyed by the code :func:`pipeline.route_catalog.route_code_sql` maps its
    route_id to, the code the feed's trips and every route aggregate carry,
    and a trip naming no route by ''. Counted once per service and multiplied
    by its running days, as trips are, so a day's routes sum to its
    ``timetable_plan.planned``."""
    return f""",
    service_route_trips AS (
        SELECT agency_id, service_id, COALESCE({route_code_sql("route_id")}, '') AS route_code, COUNT(*) AS trips
        FROM static_trips
        WHERE agency_id = ANY({agency_ids})
        GROUP BY agency_id, service_id, 3
    ),
    route_day_trips AS (
        SELECT r.agency_id, r.day, s.route_code, SUM(s.trips)::int AS trips
        FROM running r
        JOIN service_route_trips s ON s.agency_id = r.agency_id AND s.service_id = r.service_id
        GROUP BY r.agency_id, r.day, s.route_code
    ),
    timetable_route_plan AS (
        SELECT cv.agency_id, cv.day,
               COALESCE(jsonb_object_agg(rt.route_code, rt.trips) FILTER (WHERE rt.route_code IS NOT NULL), '{{}}')
                   AS by_route
        FROM covered cv
        LEFT JOIN route_day_trips rt ON rt.agency_id = cv.agency_id AND rt.day = cv.day
        GROUP BY cv.agency_id, cv.day
    )"""


# A timetable given as rows (rows_plan), each relation a CTE unnested from
# array parameters into its table's columns. A trip's stop times are expanded
# from its count, one row each, as static_stop_times holds them.
_ROWS_CTES = (
    """
    rows_trips AS (
        SELECT %(agency_id)s AS agency_id, a.trip_id, a.service_id
        FROM unnest(%(trip_ids)s::text[], %(trip_services)s::text[]) AS a(trip_id, service_id)
    ),
    rows_stop_times AS (
        SELECT %(agency_id)s AS agency_id, a.trip_id
        FROM unnest(%(trip_ids)s::text[], %(trip_stops)s::int[]) AS a(trip_id, stops), generate_series(1, a.stops)
    ),
    rows_calendar AS (
        SELECT %(agency_id)s AS agency_id, a.*
        FROM unnest(%(calendar_services)s::text[], """
    + "".join(f"%(calendar_{day})s::boolean[], " for day in WEEKDAY_COLUMNS)
    + f"""%(calendar_starts)s::date[], %(calendar_ends)s::date[])
            AS a(service_id, {", ".join(WEEKDAY_COLUMNS)}, start_date, end_date)
    ),
    rows_calendar_dates AS (
        SELECT %(agency_id)s AS agency_id, a.*
        FROM unnest(%(dates_services)s::text[], %(dates)s::text[], %(dates_exceptions)s::int[])
            AS a(service_id, date, exception_type)
    ),"""
)
_ROWS = PlanSource("rows_trips", "rows_stop_times", "rows_calendar", "rows_calendar_dates")

_ROWS_PLAN_SQL = (
    "WITH"
    + _ROWS_CTES
    + plan_ctes("ARRAY[%(agency_id)s]::int[]", "%(from_date)s::date", "%(to_date)s::date", _ROWS)
    + stop_plan_ctes("ARRAY[%(agency_id)s]::int[]", _ROWS)
    + """
    SELECT p.day, p.planned, sp.planned_stop_events
    FROM timetable_plan p
    JOIN timetable_stop_plan sp ON sp.agency_id = p.agency_id AND sp.day = p.day
    """
)


class DayPlan(NamedTuple):
    trips: int
    stop_events: int


def rows_plan(
    conn,
    agency_id: int,
    first: date,
    last: date,
    *,
    trips: Iterable[tuple[str, str | None, int]],
    calendar: Iterable[tuple],
    calendar_dates: Iterable[tuple[str, str, int | None]],
) -> dict[date, DayPlan]:
    """The planned trips and stop events of each day of [first, last] that a
    timetable given as rows covers: what the capture saves for the day while
    that timetable is loaded, from the same CTEs.

    *trips* is each trip's (trip_id, service_id, stop times), *calendar*
    ``static_calendar``'s columns after agency_id up to end_date, and
    *calendar_dates* ``static_calendar_dates``' after agency_id. A trip with
    no stop time is counted only if given, with 0. Only reads, so it runs in a
    read-only transaction. *conn* is a psycopg2 connection.
    """
    trip_rows, calendar_rows, date_rows = list(trips), list(calendar), list(calendar_dates)
    params: dict = {
        "agency_id": agency_id,
        "from_date": first,
        "to_date": last,
        "trip_ids": [row[0] for row in trip_rows],
        "trip_services": [row[1] for row in trip_rows],
        "trip_stops": [row[2] for row in trip_rows],
        "calendar_services": [row[0] for row in calendar_rows],
        "calendar_starts": [row[8] for row in calendar_rows],
        "calendar_ends": [row[9] for row in calendar_rows],
        "dates_services": [row[0] for row in date_rows],
        "dates": [row[1] for row in date_rows],
        "dates_exceptions": [row[2] for row in date_rows],
    }
    for i, day in enumerate(WEEKDAY_COLUMNS, start=1):
        params[f"calendar_{day}"] = [row[i] for row in calendar_rows]
    with conn.cursor() as cur:
        cur.execute(_ROWS_PLAN_SQL, params)
        return {day: DayPlan(planned, events) for day, planned, events in cur.fetchall()}


_TRIP_IDS_SQL = (
    "WITH"
    + plan_ctes("$1::int[]", "$2::date", "$3::date")
    + """
    SELECT cv.agency_id, cv.day, array_remove(array_agg(t.trip_id), NULL) AS trips
    FROM covered cv
    LEFT JOIN running r ON r.agency_id = cv.agency_id AND r.day = cv.day
    LEFT JOIN static_trips t ON t.agency_id = r.agency_id AND t.service_id = r.service_id
    GROUP BY cv.agency_id, cv.day
    """
)


async def planned_trip_ids(
    conn, agency_ids: Sequence[int], first: date, last: date
) -> dict[tuple[int, date], frozenset[str]]:
    """The trip_ids the loaded timetable plans on each day of [first, last] it
    covers, keyed (agency, day): none on a covered day nothing runs, under the
    running rule :func:`plan_ctes` counts by. A day it says nothing about is
    absent. *conn* is an asyncpg connection."""
    rows = await conn.fetch(_TRIP_IDS_SQL, list(agency_ids), first, last)
    return {(row["agency_id"], row["day"]): frozenset(row["trips"]) for row in rows}


def running_trip_join(agency_id: str, trip_id: str, day: str) -> str:
    """JOINs that keep a ``(trip_id, day)`` pair only when the loaded timetable
    runs that trip on that day: the running rule :func:`plan_ctes` counts
    planned trips by, applied to one trip. Needs :func:`plan_ctes` in the
    query's WITH, over a span that covers every ``day``, and binds ``t`` to the
    trip's ``static_trips`` row.

    Only the loaded timetable is kept and a reload replaces it in place, so its
    stops describe a past day only if it was the timetable that day: a day
    before it starts, or a trip_id it reuses under a service that does not run
    that day, finds no row here. The arguments are SQL expressions, as in
    :func:`plan_ctes`.
    """
    return (
        f"JOIN static_trips t ON t.agency_id = {agency_id} AND t.trip_id = {trip_id} "
        f"JOIN running r ON r.agency_id = {agency_id} AND r.service_id = t.service_id AND r.day = {day}"
    )


_CAPTURE_SQL = (
    "WITH"
    + plan_ctes("ARRAY[%(agency_id)s]::int[]", "%(from_date)s::date", "%(to_date)s::date")
    + stop_plan_ctes("ARRAY[%(agency_id)s]::int[]")
    + route_plan_ctes("ARRAY[%(agency_id)s]::int[]")
    + """
    INSERT INTO planned_trips_daily
        (agency_id, service_date, planned_trips, planned_stop_events, planned_trips_by_route)
    SELECT p.agency_id, p.day, p.planned, sp.planned_stop_events, rp.by_route
    FROM timetable_plan p
    JOIN timetable_stop_plan sp ON sp.agency_id = p.agency_id AND sp.day = p.day
    JOIN timetable_route_plan rp ON rp.agency_id = p.agency_id AND rp.day = p.day
    ON CONFLICT (agency_id, service_date) DO NOTHING
    """
)


def capture_planned_trips(conn, agency_id: int, *, today: date | None = None) -> int | None:
    """Save the planned trips of each day before ``today`` that the loaded
    timetable covers and ``planned_trips_daily`` does not hold yet, then
    commit. Returns how many days it saved, or None when the capture failed.

    ``today`` defaults to the service day in progress
    (:func:`pipeline.service_day.service_date_of`), which is left to the loaded
    timetable until it is over. See :func:`_capture` for the commit and the
    failure contract.
    """
    day = today or _service_today()
    return _capture(conn, agency_id, day, lambda: day - timedelta(days=1))


def capture_before_reload(
    conn, agency_id: int, incoming: Iterable[tuple[date, date]], *, today: date | None = None
) -> int | None:
    """Save the outgoing timetable's planned trips for the days before the
    incoming timetable's first, as :func:`capture_planned_trips` does, before a
    reload replaces the outgoing timetable.

    ``incoming`` is the day ranges the incoming timetable covers, both ends
    inclusive: each ``calendar.txt`` service's range, and each day a
    ``calendar_dates.txt`` row names as a range of one day. Days after today
    are saved too, up to ``CAPTURE_LOOKBACK_DAYS`` ahead: they would run on the
    outgoing timetable and no other would plan them.
    """
    day = today or _service_today()
    return _capture(conn, agency_id, day, lambda: _reload_through(day, list(incoming)))


def _service_today() -> date:
    return service_date_of(datetime.now(timezone.utc))


def _reload_through(day: date, incoming: list[tuple[date, date]]) -> date:
    """The last day a reload on ``day`` saves from the outgoing timetable: the
    day before the incoming timetable's first, by the coverage rule
    :func:`plan_ctes` applies within a capture's reach.

    A range that ends before it starts covers nothing, and a range that ends
    before the capture window covers nothing a capture could save; a range
    starting before the window covers the window from its start. With no
    range in reach, the outgoing timetable keeps the days up to ``day``.
    """
    window_start = day - timedelta(days=CAPTURE_LOOKBACK_DAYS - 1)
    firsts = [max(first, window_start) for first, last in incoming if window_start <= last and first <= last]
    if not firsts:
        return day
    return min(min(firsts) - timedelta(days=1), day + timedelta(days=CAPTURE_LOOKBACK_DAYS))


def _capture(conn, agency_id: int, day: date, through: Callable[[], date]) -> int | None:
    """Save the days of the capture window ending on ``through()`` that have no
    row yet, then commit.

    The commit is immediate, and takes along whatever the caller had open:
    left uncommitted behind a long load or rebuild, the new rows would make a
    concurrent capture of the same days wait for it. Everything, the date
    arithmetic on a feed's dates included, runs inside one guard, and the
    insert inside a savepoint, so a failure is logged and rolled back to it,
    costing the capture and nothing else; the next capture saves whatever days
    are still loaded. Never raises. The caller's connection must not be in
    autocommit, which has no transaction to hold a savepoint.
    """
    try:
        last = through()
        params = {
            "agency_id": agency_id,
            "from_date": day - timedelta(days=CAPTURE_LOOKBACK_DAYS - 1),
            "to_date": last,
        }
        with conn.cursor() as cur:
            cur.execute("SAVEPOINT capture_planned_trips")
            try:
                cur.execute(_CAPTURE_SQL, params)
            except Exception:
                cur.execute("ROLLBACK TO SAVEPOINT capture_planned_trips")
                raise
            saved = cur.rowcount
            cur.execute("RELEASE SAVEPOINT capture_planned_trips")
        conn.commit()
    except Exception:
        logger.warning("planned_trips_daily: could not save planned trips for agency %s", agency_id, exc_info=True)
        return None
    logger.info(f"  planned_trips_daily: {saved} day(s) saved through {last}")
    return saved
