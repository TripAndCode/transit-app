"""Supply-side headline metrics: planned trip counts and vehicle-km per
static-feed version, plus a vehicle-km-delivered rate.

``agg_static_version_summary`` (populated by ``pipeline.analyze.analyze()``)
is intentionally NOT range-scoped — one row per (agency_id,
static_version_id) describing what a single full run of that version's
entire defined schedule looks like (every ``trips.txt`` row once),
independent of how many calendar days a caller's range covers. It's the
headline "how big is this schedule" figure, not a day-range total the way
``pipeline.reports.service_delivered``'s ``planned_trips`` is.

The read path here always reports the MOST RECENTLY COMPUTED version's row
(the one ``analyze()`` last touched for this agency) as the current headline
figure — older versions' rows stay in the table (``analyze()`` only ever
upserts, never deletes, from it) purely as the historical record
``pipeline.reports.schedule_revision``'s boundary markers rely on, not read
here.
"""

from __future__ import annotations

from typing import Any

_CURRENT_VERSION_SQL = """
    SELECT DISTINCT ON (agency_id) agency_id, static_version_id, trip_count, vehicle_km
    FROM agg_static_version_summary
    WHERE agency_id = ANY($1::int[])
    ORDER BY agency_id, computed_at DESC
"""


async def compute_supply_metrics_by_agency(
    conn, agency_ids: list[int], delivered: dict[int, dict[str, Any]]
) -> dict[int, dict[str, Any]]:
    """Per-agency ``{"static_version_id", "planned_trip_count",
    "planned_vehicle_km", "vehicle_km_delivered_pct"}`` headline supply
    figures.

    ``delivered`` is ``pipeline.reports.service_delivered.
    compute_service_delivered_by_agency``'s output for the SAME agencies —
    reused rather than recomputed so this metric never disagrees with the
    executed/planned trip ratio shown alongside it.

    ``vehicle_km_delivered_pct`` is the "vehicle-km delivered" rate: item
    92's executed/planned trip ratio applied to this version's planned
    vehicle-km (there is no per-trip executed/canceled distance breakdown to
    do better than assuming a canceled trip's distance is representative of
    the agency's average). It falls back to ``None`` — a trip-count-only
    headline, via ``planned_trip_count`` alone — whenever either input isn't
    available: this agency has no ``shapes.txt`` loaded
    (``planned_vehicle_km`` is ``None``), or item 92's ratio isn't computable
    for it (``service_delivered_pct`` is ``None``).
    """
    if not agency_ids:
        return {}
    rows = await conn.fetch(_CURRENT_VERSION_SQL, agency_ids)
    by_agency = {r["agency_id"]: r for r in rows}

    result: dict[int, dict[str, Any]] = {}
    for aid in agency_ids:
        r = by_agency.get(aid)
        if r is None:
            result[aid] = {
                "static_version_id": None,
                "planned_trip_count": None,
                "planned_vehicle_km": None,
                "vehicle_km_delivered_pct": None,
            }
            continue
        planned_km = float(r["vehicle_km"]) if r["vehicle_km"] is not None else None
        service_delivered_pct = delivered.get(aid, {}).get("service_delivered_pct")
        vehicle_km_delivered_pct = (
            service_delivered_pct if (planned_km is not None and service_delivered_pct is not None) else None
        )
        result[aid] = {
            "static_version_id": r["static_version_id"],
            "planned_trip_count": int(r["trip_count"]),
            "planned_vehicle_km": round(planned_km, 1) if planned_km is not None else None,
            "vehicle_km_delivered_pct": vehicle_km_delivered_pct,
        }
    return result
