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
_PLANNED_TRIPS_SQL = """
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
    )
    SELECT r.agency_id, SUM(t.trips) AS planned
    FROM running r
    JOIN trip_counts t ON t.agency_id = r.agency_id AND t.service_id = r.service_id
    GROUP BY r.agency_id
"""

_NON_EXECUTED_TRIPS_SQL = """
    SELECT agency_id, SUM(non_executed_trips) AS non_executed
    FROM agg_service_delivered_daily
    WHERE date BETWEEN $1 AND $2
    GROUP BY agency_id
"""


async def compute_service_delivered_by_agency(
    conn, agency_ids: list[int], from_date: date, to_date: date
) -> dict[int, dict[str, Any]]:
    """Per-agency ``{"planned_trips", "executed_trips", "service_delivered_pct"}``
    over ``[from_date, to_date]``.

    ``planned_trips`` is always a real (possibly zero) integer count.
    ``executed_trips``/``service_delivered_pct`` are ``None`` together
    whenever the ratio isn't computable for this agency/range (see module
    docstring) — never silently 0 canceled / 100% delivered. Both queries
    below read the loaded static schedule and a precomputed aggregate in
    Postgres — no ClickHouse access on this read path.
    """
    if not agency_ids:
        return {}
    planned_rows = await conn.fetch(_PLANNED_TRIPS_SQL, agency_ids, from_date, to_date)
    planned = {r["agency_id"]: int(r["planned"]) for r in planned_rows}

    # agg_service_delivered_daily only carries a row for a (agency, date) that
    # actually had a non-executed trip -- a day with zero cancellations has no
    # row at all, so an agency with no row in range still legitimately reads
    # non_executed=0 via the .get(..., 0) default below, not "not available".
    non_executed_rows = await conn.fetch(_NON_EXECUTED_TRIPS_SQL, from_date, to_date)
    non_executed = {r["agency_id"]: int(r["non_executed"]) for r in non_executed_rows}

    # An agency must satisfy both halves of the shared gate: an ingest
    # strategy that CAN send this field, and a live probe verdict saying its
    # own feed actually DOES. The batch helper resolves both in one query so
    # this read path can't drift from the per-agency callers.
    populated_ids = await rt_field_coverage_confirmed_agencies(conn, agency_ids)

    result: dict[int, dict[str, Any]] = {}
    for aid in agency_ids:
        p = planned.get(aid, 0)
        if aid not in populated_ids or p <= 0:
            result[aid] = {"planned_trips": p, "executed_trips": None, "service_delivered_pct": None}
            continue
        # Clamp at 0: a canceled/skipped trip-day whose trip_id doesn't
        # actually match anything in the static schedule (feed drift, or a
        # RT-only ADDED trip marked CANCELED) must not drive the ratio
        # negative.
        executed = max(p - non_executed.get(aid, 0), 0)
        result[aid] = {
            "planned_trips": p,
            "executed_trips": executed,
            "service_delivered_pct": round(executed / p * 100, 1),
        }
    return result
