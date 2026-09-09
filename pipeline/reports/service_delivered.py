"""Service-delivered rate: executed trips ÷ planned trips from the static
GTFS schedule.

``planned_trips`` is the number of (service day, trip_id) instances the
static schedule says should run over the range — from Postgres
``static_calendar_dates`` (``exception_type = 1`` rows only; this codebase's
GTFS feeds enumerate running days via ``calendar_dates.txt`` explicitly,
never a weekly ``calendar.txt``) joined to ``static_trips``.

``executed_trips`` is ``planned_trips`` minus the trip-days GTFS-RT reports
as not executed — identified via ``schedule_relationship_trip = CANCELED``
and any populated ``schedule_relationship_stop = SKIPPED``, precomputed per
day into Postgres ``agg_service_delivered_daily`` by
``pipeline.analyze.analyze()`` (see that builder for the ClickHouse query
this reads from, which lives against raw `updates`, not this module).

Both ``executed_trips`` and ``service_delivered_pct`` are ``None`` ("not
available") rather than a misleadingly perfect 100% whenever the ratio isn't
computable: only agencies in
``pipeline.strategies.static_join.RT_FIELD_COVERAGE_CONFIRMED_AGENCIES``
have been confirmed to actually populate ``schedule_relationship_trip`` on
their live feed (``aomori_regex`` always leaves it NULL, and an
``ingest_strategy == 'static_join'`` agency outside that confirmed set is
untrusted until probed -- sharing the JOIN mechanism doesn't imply sharing
field coverage, see that module's docstring), or there is no static schedule
to plan against (``planned_trips == 0``).
"""

from __future__ import annotations

from datetime import date
from typing import Any

from pipeline.strategies.static_join import RT_FIELD_COVERAGE_CONFIRMED_AGENCIES, RT_INGEST_STRATEGIES

_PLANNED_TRIPS_SQL = """
    SELECT cd.agency_id, COUNT(*) AS planned
    FROM static_calendar_dates cd
    JOIN static_trips t ON t.agency_id = cd.agency_id AND t.service_id = cd.service_id
    WHERE cd.exception_type = 1
      AND cd.date BETWEEN $1 AND $2
    GROUP BY cd.agency_id
"""

_NON_EXECUTED_TRIPS_SQL = """
    SELECT agency_id, SUM(non_executed_trips) AS non_executed
    FROM agg_service_delivered_daily
    WHERE date BETWEEN $1 AND $2
    GROUP BY agency_id
"""

# Real ingest_strategy check, kept as defense in depth alongside the
# RT_FIELD_COVERAGE_CONFIRMED_AGENCIES intersection below -- an agency must
# satisfy both: the ingest strategy that CAN send this field (RT_INGEST_
# STRATEGIES), and the explicit confirmed-set gate that says it actually
# DOES on its live feed.
_POPULATED_AGENCIES_SQL = "SELECT agency_id FROM agencies WHERE ingest_strategy = ANY($1::text[])"


async def compute_service_delivered_by_agency(
    conn, agency_ids: list[int], from_date: date, to_date: date
) -> dict[int, dict[str, Any]]:
    """Per-agency ``{"planned_trips", "executed_trips", "service_delivered_pct"}``
    over ``[from_date, to_date]``.

    ``planned_trips`` is always a real (possibly zero) integer count.
    ``executed_trips``/``service_delivered_pct`` are ``None`` together
    whenever the ratio isn't computable for this agency/range (see module
    docstring) — never silently 0 canceled / 100% delivered. Both queries
    below are indexed range scans on precomputed Postgres aggregates — no
    ClickHouse access on this read path.
    """
    if not agency_ids:
        return {}
    # static_calendar_dates.date is GTFS's raw calendar_dates.txt text, always
    # YYYYMMDD (8 zero-padded digits) — lexicographic BETWEEN on that fixed
    # width agrees with calendar ordering, so no cast to a Postgres DATE is
    # needed (nor possible: some rows may have non-parseable garbage from a
    # malformed feed, which a DATE cast would fail hard on for every agency).
    from_str, to_str = from_date.strftime("%Y%m%d"), to_date.strftime("%Y%m%d")
    planned_rows = await conn.fetch(_PLANNED_TRIPS_SQL, from_str, to_str)
    planned = {r["agency_id"]: int(r["planned"]) for r in planned_rows}

    # agg_service_delivered_daily only carries a row for a (agency, date) that
    # actually had a non-executed trip -- a day with zero cancellations has no
    # row at all, so an agency with no row in range still legitimately reads
    # non_executed=0 via the .get(..., 0) default below, not "not available".
    non_executed_rows = await conn.fetch(_NON_EXECUTED_TRIPS_SQL, from_date, to_date)
    non_executed = {r["agency_id"]: int(r["non_executed"]) for r in non_executed_rows}

    # Intersect the real ingest_strategy check against the explicit
    # pipeline.strategies.static_join.RT_FIELD_COVERAGE_CONFIRMED_AGENCIES
    # confirmed-set gate -- an agency must satisfy both, not just the ingest
    # strategy that merely makes this field possible to send.
    populated_rows = await conn.fetch(_POPULATED_AGENCIES_SQL, list(RT_INGEST_STRATEGIES))
    populated_ids = {r["agency_id"] for r in populated_rows} & RT_FIELD_COVERAGE_CONFIRMED_AGENCIES

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
