"""Service-delivered rate: executed trips ÷ planned trips from the static
GTFS schedule.

``planned_trips`` is the number of (service day, trip_id) instances the
static schedule says should run over the range. A service runs on a date
under the GTFS rule :mod:`pipeline.day_types` also applies: its
``static_calendar`` (``calendar.txt``) range covers the date with that
weekday's flag set and no ``static_calendar_dates`` removal
(``exception_type = 2``) names the date, or a ``static_calendar_dates``
addition (``exception_type = 1``) names it. A service both cases put on the
same date runs once. Each running (service, date) contributes the number of
``static_trips`` rows under that service.

``executed_trips`` is ``planned_trips`` minus the trip-days GTFS-RT reports
as not executed — identified via ``schedule_relationship_trip = CANCELED``
and any populated ``schedule_relationship_stop = SKIPPED``, precomputed per
day into Postgres ``agg_service_delivered_daily`` by
``pipeline.analyze.analyze()`` (see that builder for the ClickHouse query
this reads from, which lives against raw `updates`, not this module).

Both ``executed_trips`` and ``service_delivered_pct`` are ``None`` ("not
available") rather than a misleadingly perfect 100% whenever the ratio isn't
computable: only an agency with a live RT field-coverage verdict (see
``pipeline.strategies.static_join.rt_field_coverage_confirmed_agencies``)
has been confirmed to actually populate ``schedule_relationship_trip`` on
its live feed (``aomori_regex`` always leaves it NULL, and an
``ingest_strategy == 'static_join'`` agency with no live verdict is
untrusted until probed -- sharing the JOIN mechanism doesn't imply sharing
field coverage, see that module's docstring), or there is no static schedule
to plan against (``planned_trips == 0``).
"""

from __future__ import annotations

from datetime import date
from typing import Any

from pipeline.strategies.static_join import rt_field_coverage_confirmed_agencies

# static_calendar_dates.date is the feed's raw YYYYMMDD text and may hold a
# malformed row, which a DATE cast would fail on for every agency. The
# fixed-width text compares in calendar order, so BETWEEN bounds it, and
# matching it to the generated series' text drops a malformed row the
# lexicographic bounds would let through. Trips are counted once per service
# and multiplied by its running days, never joined row-per-trip-per-day.
#
# `covered` is the days the loaded timetable says anything about: inside a
# calendar.txt service's range, or named by a calendar_dates.txt row. A reload
# replaces the agency's calendar, so a window reaching back before the load has
# days no plan covers, and missed trips count only on covered days, the ones the
# plan counted. agg_service_delivered_daily has a row only for a day with a
# missed trip, so an agency with none reads 0, not "not available".
_SERVICE_SQL = """
    WITH days AS (
        SELECT day, to_char(day, 'YYYYMMDD') AS ymd, EXTRACT(ISODOW FROM day)::int AS isodow
        FROM (SELECT $2::date + i AS day FROM generate_series(0, $3::date - $2::date) AS i) AS d
    ),
    trip_counts AS (
        SELECT agency_id, service_id, COUNT(*) AS trips
        FROM static_trips
        WHERE agency_id = ANY($1::int[])
        GROUP BY agency_id, service_id
    ),
    running AS (
        SELECT c.agency_id, c.service_id, days.ymd
        FROM static_calendar c
        JOIN days ON days.day BETWEEN c.start_date AND c.end_date
        WHERE c.agency_id = ANY($1::int[])
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
        SELECT cd.agency_id, cd.service_id, days.ymd
        FROM static_calendar_dates cd
        JOIN days ON days.ymd = cd.date
        WHERE cd.agency_id = ANY($1::int[])
          AND cd.exception_type = 1
          AND cd.date BETWEEN to_char($2::date, 'YYYYMMDD') AND to_char($3::date, 'YYYYMMDD')
    ),
    covered AS (
        SELECT c.agency_id, days.day
        FROM static_calendar c
        JOIN days ON days.day BETWEEN c.start_date AND c.end_date
        WHERE c.agency_id = ANY($1::int[])
        UNION
        SELECT cd.agency_id, days.day
        FROM static_calendar_dates cd
        JOIN days ON days.ymd = cd.date
        WHERE cd.agency_id = ANY($1::int[])
          AND cd.date BETWEEN to_char($2::date, 'YYYYMMDD') AND to_char($3::date, 'YYYYMMDD')
    ),
    planned AS (
        SELECT r.agency_id, SUM(t.trips) AS planned
        FROM running r
        JOIN trip_counts t ON t.agency_id = r.agency_id AND t.service_id = r.service_id
        GROUP BY r.agency_id
    ),
    coverage AS (
        SELECT agency_id, COUNT(*) AS covered_days FROM covered GROUP BY agency_id
    ),
    missed AS (
        SELECT s.agency_id, SUM(s.non_executed_trips) AS non_executed
        FROM agg_service_delivered_daily s
        JOIN covered c ON c.agency_id = s.agency_id AND c.day = s.date
        GROUP BY s.agency_id
    )
    SELECT a.agency_id,
           COALESCE(p.planned, 0) AS planned,
           COALESCE(cv.covered_days, 0) AS covered_days,
           COALESCE(m.non_executed, 0) AS non_executed
    FROM unnest($1::int[]) AS a(agency_id)
    LEFT JOIN planned p USING (agency_id)
    LEFT JOIN coverage cv USING (agency_id)
    LEFT JOIN missed m USING (agency_id)
"""


async def compute_service_delivered_by_agency(
    conn, agency_ids: list[int], from_date: date, to_date: date
) -> dict[int, dict[str, Any]]:
    """Per-agency ``{"planned_trips", "executed_trips", "service_delivered_pct",
    "covered_days", "window_days"}`` over ``[from_date, to_date]``.

    ``covered_days`` counts the window's days the loaded timetable covers;
    both sides of the ratio count only those days.

    ``planned_trips`` is always a real (possibly zero) integer count.
    ``executed_trips``/``service_delivered_pct`` are ``None`` together
    whenever the ratio isn't computable for this agency/range (see module
    docstring) — never silently 0 canceled / 100% delivered. One query reads
    the loaded static schedule and a precomputed aggregate in Postgres — no
    ClickHouse access on this read path.
    """
    if not agency_ids:
        return {}
    window_days = (to_date - from_date).days + 1
    rows = {r["agency_id"]: r for r in await conn.fetch(_SERVICE_SQL, agency_ids, from_date, to_date)}

    # An agency must satisfy both halves of the shared gate: an ingest
    # strategy that CAN send this field, and a live probe verdict saying its
    # own feed actually DOES. The batch helper resolves both in one query so
    # this read path can't drift from the per-agency callers.
    populated_ids = await rt_field_coverage_confirmed_agencies(conn, agency_ids)

    result: dict[int, dict[str, Any]] = {}
    for aid in agency_ids:
        p = int(rows[aid]["planned"])
        coverage = {"covered_days": int(rows[aid]["covered_days"]), "window_days": window_days}
        if aid not in populated_ids or p <= 0:
            result[aid] = {"planned_trips": p, "executed_trips": None, "service_delivered_pct": None, **coverage}
            continue
        # Clamp at 0: a canceled/skipped trip-day whose trip_id doesn't
        # actually match anything in the static schedule (feed drift, or a
        # RT-only ADDED trip marked CANCELED) must not drive the ratio
        # negative.
        executed = max(p - int(rows[aid]["non_executed"]), 0)
        result[aid] = {
            "planned_trips": p,
            "executed_trips": executed,
            "service_delivered_pct": round(executed / p * 100, 1),
            **coverage,
        }
    return result
