"""Service-delivered rate: executed trips ÷ planned trips from the static
GTFS schedule.

``planned_trips`` is the number of (service day, trip_id) instances the
schedule says should run over the range, counted per day by
:mod:`pipeline.planned_trips`: a day's count saved in ``planned_trips_daily``
while its timetable was loaded, or else the count the loaded timetable plans
for it.

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

from pipeline.planned_trips import plan_ctes
from pipeline.strategies.static_join import rt_field_coverage_confirmed_agencies

# A day has a plan when planned_trips_daily saved one for it or the loaded
# timetable covers it, and the saved count wins: it is what the timetable in
# force on the day planned, where a later reload may plan the day differently
# or not at all. Only those days count, on both sides of the ratio. A reload
# replaces the agency's calendar, so a window can reach back to days no plan
# covers, and missed trips count only on the days the plan counted.
# agg_service_delivered_daily is built from GTFS-RT alone, so a saved day's
# missed trips are there whatever timetable is loaded now. It has a row only
# for a day with a missed trip, so an agency with none reads 0, not "not
# available".
_SERVICE_SQL = (
    "WITH"
    + plan_ctes("$1::int[]", "$2::date", "$3::date")
    + """,
    saved AS (
        SELECT agency_id, service_date AS day, planned_trips AS planned
        FROM planned_trips_daily
        WHERE agency_id = ANY($1::int[]) AND service_date BETWEEN $2::date AND $3::date
    ),
    planned_days AS (
        SELECT agency_id, day, planned FROM saved
        UNION ALL
        SELECT tp.agency_id, tp.day, tp.planned
        FROM timetable_plan tp
        WHERE NOT EXISTS (SELECT 1 FROM saved s WHERE s.agency_id = tp.agency_id AND s.day = tp.day)
    ),
    plan AS (
        SELECT agency_id, SUM(planned) AS planned, COUNT(*) AS covered_days
        FROM planned_days
        GROUP BY agency_id
    ),
    missed AS (
        SELECT s.agency_id, SUM(s.non_executed_trips) AS non_executed
        FROM agg_service_delivered_daily s
        JOIN planned_days p ON p.agency_id = s.agency_id AND p.day = s.date
        GROUP BY s.agency_id
    )
    SELECT a.agency_id,
           COALESCE(p.planned, 0) AS planned,
           COALESCE(p.covered_days, 0) AS covered_days,
           COALESCE(m.non_executed, 0) AS non_executed
    FROM unnest($1::int[]) AS a(agency_id)
    LEFT JOIN plan p USING (agency_id)
    LEFT JOIN missed m USING (agency_id)
"""
)


async def compute_service_delivered_by_agency(
    conn, agency_ids: list[int], from_date: date, to_date: date
) -> dict[int, dict[str, Any]]:
    """Per-agency ``{"planned_trips", "executed_trips", "service_delivered_pct",
    "covered_days", "window_days"}`` over ``[from_date, to_date]``.

    ``covered_days`` counts the window's days with a plan, saved in
    ``planned_trips_daily`` or covered by the loaded timetable; both sides of
    the ratio count only those days.

    ``planned_trips`` is always a real (possibly zero) integer count.
    ``executed_trips``/``service_delivered_pct`` are ``None`` together
    whenever the ratio isn't computable for this agency/range (see module
    docstring) — never silently 0 canceled / 100% delivered. One query reads
    the saved and loaded plans and a precomputed aggregate in Postgres — no
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
