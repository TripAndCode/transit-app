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

from datetime import date, datetime
from typing import NamedTuple

from pipeline.cache import async_lru_cache
from pipeline.db import hms_to_sec_sql
from pipeline.planned_trips import plan_ctes
from pipeline.service_day import service_day_moment_utc, service_day_seconds_sql

# A stop with no readable time is passed over. A trip leaves its first stop at
# its departure time and reaches its last at its arrival time; a stop giving
# only one of the two stands for both.
_SERVICE_HOURS_SQL = (
    "WITH"
    + plan_ctes("ARRAY[$1::int]", "$2::date", "$2::date")
    + f""",
    stop_seconds AS (
        SELECT {hms_to_sec_sql("s.departure_time")} AS departure, {hms_to_sec_sql("s.arrival_time")} AS arrival
        FROM running r
        JOIN static_trips t ON t.agency_id = $1 AND t.agency_id = r.agency_id AND t.service_id = r.service_id
        JOIN static_stop_times s ON s.agency_id = t.agency_id AND s.trip_id = t.trip_id
    )
    SELECT MIN({service_day_seconds_sql("COALESCE(departure, arrival)")}) AS first_departure,
           MAX({service_day_seconds_sql("COALESCE(arrival, departure)")}) AS last_arrival
    FROM stop_seconds
    """
)


class ServiceHours(NamedTuple):
    """The first departure and the last arrival of a service day, as UTC instants."""

    first_departure_at: datetime
    last_arrival_at: datetime


# The hours change only when a timetable load replaces the calendar or the
# stop times, and a load in this process forgets them at once
# (:func:`invalidate_service_hours`). The TTL bounds how long a load run by
# another process takes to show.
@async_lru_cache(maxsize=64, ttl_seconds=300)
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
