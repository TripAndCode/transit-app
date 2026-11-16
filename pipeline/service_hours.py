"""A service day's hours, as the loaded timetable plans them.

They run from the first departure to the last arrival among the trips the
calendar runs that day. Which trips run is the ``running`` rule of
:func:`pipeline.planned_trips.plan_ctes`, the one the planned trip counts
apply. A time before the cutover hour, written at face value (``00:40``), is
the same night's, as GTFS's extended hours (``24:40``) already say
(:func:`pipeline.service_day.service_day_seconds`), so it can end a day but
never start one.

The Live map reads these hours to tell a timetable with nothing running from a
feed that stopped.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime
from typing import NamedTuple

from pipeline.cache import async_lru_cache
from pipeline.db import hms_to_sec_sql
from pipeline.planned_trips import ROWS_CALENDAR_CTES, PlanSource, calendar_params, plan_ctes
from pipeline.service_day import service_day_moment_utc, service_day_seconds_sql

_DEPARTURE = hms_to_sec_sql("s.departure_time")
_ARRIVAL = hms_to_sec_sql("s.arrival_time")
# A day's hours from its running trips' ends, each a trip_ends ``starts`` and
# ``ends`` in seconds from midnight as written.
_HOURS = (
    f"MIN({service_day_seconds_sql('starts')}) AS first_departure, "
    f"MAX({service_day_seconds_sql('ends')}) AS last_arrival"
)

# A trip runs from its first stop's departure to its last stop's arrival (a
# stop with one time stands for both; an untimed end is left out). Each end is
# one stop_times primary-key probe, so only two stops per trip are parsed;
# MATERIALIZED keeps it one probe per end, which the service-day shift's
# repeated references would otherwise re-run.
_SERVICE_HOURS_SQL = (
    "WITH"
    + plan_ctes("ARRAY[$1::int]", "$2::date", "$2::date")
    + f""",
    trip_ends AS MATERIALIZED (
        SELECT
            (SELECT COALESCE({_DEPARTURE}, {_ARRIVAL}) FROM static_stop_times s
             WHERE s.agency_id = t.agency_id AND s.trip_id = t.trip_id
             ORDER BY s.stop_sequence LIMIT 1) AS starts,
            (SELECT COALESCE({_ARRIVAL}, {_DEPARTURE}) FROM static_stop_times s
             WHERE s.agency_id = t.agency_id AND s.trip_id = t.trip_id
             ORDER BY s.stop_sequence DESC LIMIT 1) AS ends
        FROM running r
        JOIN static_trips t ON t.agency_id = $1 AND t.agency_id = r.agency_id AND t.service_id = r.service_id
    )
    SELECT {_HOURS}
    FROM trip_ends
    """
)

# The same hours of each day a timetable given as rows covers: its calendar
# (planned_trips.ROWS_CALENDAR_CTES) and each trip's first and last stop
# times, as hours_of takes them. A covered day nothing timed runs on reads NULL.
_ROWS_HOURS_SQL = (
    """WITH
    rows_trips AS (
        SELECT %(agency_id)s AS agency_id, a.*
        FROM unnest(%(trip_ids)s::text[], %(trip_services)s::text[], %(first_departures)s::text[],
                    %(first_arrivals)s::text[], %(last_arrivals)s::text[], %(last_departures)s::text[])
            AS a(trip_id, service_id, first_departure, first_arrival, last_arrival, last_departure)
    ),"""
    + ROWS_CALENDAR_CTES
    + plan_ctes(
        "ARRAY[%(agency_id)s]::int[]",
        "%(from_date)s::date",
        "%(to_date)s::date",
        PlanSource("rows_trips", "rows_trips", "rows_calendar", "rows_calendar_dates"),
    )
    + f""",
    trip_ends AS (
        SELECT r.day,
               COALESCE({hms_to_sec_sql("t.first_departure")}, {hms_to_sec_sql("t.first_arrival")}) AS starts,
               COALESCE({hms_to_sec_sql("t.last_arrival")}, {hms_to_sec_sql("t.last_departure")}) AS ends
        FROM running r
        JOIN rows_trips t ON t.service_id = r.service_id
    )
    SELECT cv.day, {_HOURS}
    FROM covered cv
    LEFT JOIN trip_ends te ON te.day = cv.day
    GROUP BY cv.day
    """
)


class TripEnds(NamedTuple):
    """A trip's service and the times of its first and last stop times, as
    the feed wrote them: a stop with one time stands for both."""

    trip_id: str
    service_id: str | None
    first_departure: str | None
    first_arrival: str | None
    last_arrival: str | None
    last_departure: str | None


def hours_of(
    conn,
    agency_id: int,
    first: date,
    last: date,
    *,
    trips: Iterable[TripEnds],
    calendar: Iterable[tuple],
    calendar_dates: Iterable[tuple[str, str, int | None]],
) -> dict[date, tuple[int, int] | None]:
    """The hours of each day of [first, last] that a timetable given as rows
    covers, as (first departure, last arrival) in service-day seconds, by the
    rule the loaded timetable's are read by; None on a covered day with no
    timed trip running. *calendar* and *calendar_dates* are as
    :func:`pipeline.planned_trips.calendar_params` takes them. Only reads.
    *conn* is a psycopg2 connection."""
    rows = list(trips)
    params = {
        "agency_id": agency_id,
        "from_date": first,
        "to_date": last,
        "trip_ids": [t.trip_id for t in rows],
        "trip_services": [t.service_id for t in rows],
        "first_departures": [t.first_departure for t in rows],
        "first_arrivals": [t.first_arrival for t in rows],
        "last_arrivals": [t.last_arrival for t in rows],
        "last_departures": [t.last_departure for t in rows],
        **calendar_params(calendar, calendar_dates),
    }
    with conn.cursor() as cur:
        cur.execute(_ROWS_HOURS_SQL, params)
        return {
            day: None if starts is None or ends is None else (int(starts), int(ends))
            for day, starts, ends in cur.fetchall()
        }


class ServiceHours(NamedTuple):
    """The first departure and the last arrival of a service day, as UTC instants."""

    first_departure_at: datetime
    last_arrival_at: datetime


# The hours change only when a timetable load replaces the calendar or the
# stop times, and a load in this process forgets them at once
# (:func:`invalidate_service_hours`). The TTL bounds how long a load run by
# another process takes to show.
@async_lru_cache(maxsize=64, ttl_seconds=300, agency_arg="agency_id")
async def load_service_hours(conn, agency_id: int, day: date) -> ServiceHours | None:
    """Service day ``day``'s hours for ``agency_id``, or None when the loaded
    timetable runs no timed trip that day: it does not cover the day, or covers
    it with nothing running.
    """
    row = await conn.fetchrow(_SERVICE_HOURS_SQL, agency_id, day)
    if row is None or row["first_departure"] is None or row["last_arrival"] is None:
        return None
    return ServiceHours(
        first_departure_at=service_day_moment_utc(day, row["first_departure"]),
        last_arrival_at=service_day_moment_utc(day, row["last_arrival"]),
    )


def invalidate_service_hours() -> None:
    """Forget every cached day's hours, after a load replaced an agency's timetable."""
    load_service_hours.cache_clear()  # type: ignore[attr-defined]
