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
lands. ``planned_trips_daily`` keeps each day's count as the timetable loaded
at the time planned it. :func:`capture_planned_trips` writes it before a
reload replaces anything and on every analyze run, so each day is saved while
its timetable is loaded, and the first count saved for a day stands. Days
before the first capture are not reconstructed: the reload that replaced their
timetable erased them.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from api.range import MAX_RANGE_DAYS
from pipeline.service_day import service_date_of

logger = logging.getLogger(__name__)

#: How far back a capture looks for days it has not saved yet: the longest
#: report window, ending today. Every capture after the first finds only the
#: days since the one before, so this bounds only an agency's first capture
#: and the catch-up after a long gap.
CAPTURE_LOOKBACK_DAYS = MAX_RANGE_DAYS


def plan_ctes(agency_ids: str, from_date: str, to_date: str) -> str:
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
        FROM static_trips
        WHERE agency_id = ANY({agency_ids})
        GROUP BY agency_id, service_id
    ),
    running AS (
        SELECT c.agency_id, c.service_id, days.day
        FROM static_calendar c
        JOIN days ON days.day BETWEEN c.start_date AND c.end_date
        WHERE c.agency_id = ANY({agency_ids})
          AND (ARRAY[c.monday, c.tuesday, c.wednesday, c.thursday, c.friday, c.saturday, c.sunday])[days.isodow]
          AND NOT EXISTS (
              SELECT 1
              FROM static_calendar_dates x
              WHERE x.agency_id = c.agency_id
                AND x.service_id = c.service_id
                AND x.date = days.ymd
                AND x.exception_type = 2
          )
        UNION
        SELECT cd.agency_id, cd.service_id, days.day
        FROM static_calendar_dates cd
        JOIN days ON days.ymd = cd.date
        WHERE cd.agency_id = ANY({agency_ids})
          AND cd.exception_type = 1
          AND cd.date BETWEEN to_char({from_date}, 'YYYYMMDD') AND to_char({to_date}, 'YYYYMMDD')
    ),
    covered AS (
        SELECT c.agency_id, days.day
        FROM static_calendar c
        JOIN days ON days.day BETWEEN c.start_date AND c.end_date
        WHERE c.agency_id = ANY({agency_ids})
        UNION
        SELECT cd.agency_id, days.day
        FROM static_calendar_dates cd
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


_CAPTURE_SQL = (
    "WITH"
    + plan_ctes("ARRAY[%(agency_id)s]::int[]", "%(from_date)s::date", "%(to_date)s::date")
    + """
    INSERT INTO planned_trips_daily (agency_id, service_date, planned_trips)
    SELECT agency_id, day, planned FROM timetable_plan
    ON CONFLICT (agency_id, service_date) DO NOTHING
    """
)


def capture_planned_trips(conn, agency_id: int, *, today: date | None = None) -> int | None:
    """Save the planned trips of each day up to ``today`` that the loaded
    timetable covers and ``planned_trips_daily`` does not hold yet. Returns how
    many days it saved, or None when the capture failed.

    ``today`` defaults to the service day in progress
    (:func:`pipeline.service_day.service_date_of`); a later day's plan can
    still change before it runs, so it is never saved ahead.

    Runs inside a savepoint on the caller's transaction and never commits:
    a reload saves the old timetable's days in the same transaction that
    replaces it. A failure is logged and rolled back to the savepoint, so it
    costs the capture and nothing else; the next capture saves whatever days
    are still loaded. The caller's connection must not be in autocommit, which
    has no transaction to hold a savepoint.
    """
    day = today or service_date_of(datetime.now(timezone.utc))
    params = {"agency_id": agency_id, "from_date": day - timedelta(days=CAPTURE_LOOKBACK_DAYS - 1), "to_date": day}
    try:
        with conn.cursor() as cur:
            cur.execute("SAVEPOINT capture_planned_trips")
            try:
                cur.execute(_CAPTURE_SQL, params)
            except Exception:
                cur.execute("ROLLBACK TO SAVEPOINT capture_planned_trips")
                raise
            saved = cur.rowcount
            cur.execute("RELEASE SAVEPOINT capture_planned_trips")
    except Exception:
        logger.warning("planned_trips_daily: could not save planned trips for agency %s", agency_id, exc_info=True)
        return None
    logger.info(f"  planned_trips_daily: {saved} day(s) saved through {day}")
    return saved
