"""Per-route "minimum performance standard" bonus/malus simulation (item
104, depends on item 94's Excess Waiting Time and item 98's vehicle-km
delivered rate).

``route_performance_standards`` (migration 0041) is a small, manually
populated policy-input table -- the same convention as ``ridership_weights``
(migration 0035): a performance standard's threshold and bonus/malus rate
are a negotiated contractual input, not something derivable from GTFS/GTFS-RT
data, so there is no ingestion pipeline or CRUD API for it here.

This module is explicitly an INTERNAL SIMULATION, never an actual invoice or
contractual output -- it estimates what a bonus/malus mechanism keyed off
already-computed analytics figures would produce, using this pipeline's own
approximations of those figures (which have their own documented gaps, e.g.
``vehicle_km_delivered_pct``'s trip-ratio proxy -- see
``pipeline.reports.supply``). Every caller (API response, CSV export, UI)
must keep the "simulation / estimate, not an invoice" framing attached; see
``simulation_disclaimer``.

Achievement rate and bonus/deduction formula
---------------------------------------------
For a configured standard, let ``actual`` be the resolved current value of
its ``metric_type`` and ``threshold`` be ``threshold_value``. The relative
deviation between the two is defined so that a positive value always means
"performed better than the standard" and a negative value always means
"missed the standard", regardless of whether the underlying metric is
lower-is-better (``ewt_sec``) or higher-is-better
(``vehicle_km_delivered_pct``):

    lower-is-better:  relative_deviation = (threshold - actual) / threshold
    higher-is-better: relative_deviation = (actual - threshold) / threshold

``achievement_rate = 1.0 + relative_deviation`` -- exactly 1.0 (100%) when
``actual == threshold``. ``estimated_bonus_deduction = bonus_malus_rate *
relative_deviation`` -- positive is an estimated bonus, negative an
estimated deduction (malus), exactly 0 at the threshold.

``threshold == 0`` makes the ratio's denominator meaningless (there is no
sensible "percent of zero" to express performance relative to a zero
standard), so both fields surface as ``None`` in that case -- consistent
with this codebase's rule of returning ``None`` rather than a misleadingly
precise number whenever a ratio isn't computable (see
``pipeline.reports.service_delivered``).
"""

from __future__ import annotations

from typing import Any, Literal

from api.range import RangeCtx
from pipeline.reports.headway_quality import compute_headway_quality
from pipeline.reports.service_delivered import compute_service_delivered_by_agency
from pipeline.reports.supply import compute_supply_metrics_by_agency

MetricType = Literal["ewt_sec", "vehicle_km_delivered_pct"]

# Metrics for which a SMALLER actual value is better performance. Any
# metric_type not listed here is treated as higher-is-better.
_LOWER_IS_BETTER: frozenset[str] = frozenset({"ewt_sec"})

_STANDARDS_SQL = """
    SELECT route_code, metric_type, threshold_value, bonus_malus_rate
    FROM route_performance_standards
    WHERE agency_id = $1
    ORDER BY route_code, metric_type
"""

# Server-rendered disclaimer text, following the module-level `_LOCALES`
# dict-keyed-by-locale idiom used elsewhere for caveats attached to a report
# payload (see pipeline.query.formatter's `_LOCALES`) -- the text always
# arrives pre-localized so no frontend i18n key is needed to render it
# as-is, only to label the surrounding UI chrome.
_DISCLAIMER: dict[str, str] = {
    "ja": ("これは内部シミュレーション（試算）であり、実際の請求書や契約上の成果物ではありません。"),
    "en": ("This is an internal simulation/estimate, not an actual invoice or contractual output."),
}


def simulation_disclaimer(locale: str) -> str:
    """Localized "simulation, not an invoice" caveat -- always attach this
    to any response/export surfacing performance-standard figures."""
    return _DISCLAIMER.get(locale, _DISCLAIMER["ja"])


def _relative_deviation(metric_type: str, threshold: float, actual: float) -> float | None:
    if threshold == 0:
        return None
    if metric_type in _LOWER_IS_BETTER:
        return (threshold - actual) / threshold
    return (actual - threshold) / threshold


async def compute_performance_standards(agency_id: int, ctx: RangeCtx, conn) -> list[dict[str, Any]]:
    """Every configured ``route_performance_standards`` row for *agency_id*,
    joined against the current actual value of its ``metric_type`` over
    *ctx*'s date range, with the resulting achievement rate and estimated
    bonus/deduction.

    An agency with zero configured rows returns an empty list -- "not
    configured", never a fabricated standard. A configured row whose actual
    value can't be resolved for this range (e.g. an ``ewt_sec`` standard on
    a route that isn't classified high-frequency, or that has no in-range
    headway data yet) is still returned, with ``actual_value``,
    ``achievement_rate`` and ``estimated_bonus_deduction`` all ``None`` --
    "insufficient data", not a silent zero.
    """
    rows = await conn.fetch(_STANDARDS_SQL, agency_id)
    if not rows:
        return []

    # Resolve every actual value this agency's configured rows could need,
    # up front -- one pass per metric_type family rather than a query per
    # row, mirroring compute_headway_quality/compute_supply_metrics_by_agency's
    # own per-agency batching.
    ewt_by_route: dict[str, float | None] = {}
    if any(r["metric_type"] == "ewt_sec" for r in rows):
        quality_rows = await compute_headway_quality(agency_id, ctx, conn)
        ewt_by_route = {q["route_code"]: q["ewt_sec"] for q in quality_rows}

    vehicle_km_delivered_pct: float | None = None
    if any(r["metric_type"] == "vehicle_km_delivered_pct" for r in rows):
        delivered = await compute_service_delivered_by_agency(conn, [agency_id], ctx.from_date, ctx.to_date)
        supply = await compute_supply_metrics_by_agency(conn, [agency_id], delivered)
        vehicle_km_delivered_pct = supply.get(agency_id, {}).get("vehicle_km_delivered_pct")

    out: list[dict[str, Any]] = []
    for r in rows:
        metric_type = r["metric_type"]
        threshold = float(r["threshold_value"])
        bonus_malus_rate = float(r["bonus_malus_rate"])

        if metric_type == "ewt_sec":
            actual = ewt_by_route.get(r["route_code"])
            metric_scope: Literal["route", "agency"] = "route"
        else:
            actual = vehicle_km_delivered_pct
            # vehicle_km_delivered_pct has no per-route breakdown (see
            # pipeline.reports.supply's module docstring) -- every route
            # configured with this metric_type is compared against its
            # agency's own rate, which callers must surface (see
            # PerformanceStandardRow.metric_scope), not present as if it
            # were route-specific.
            metric_scope = "agency"

        achievement_rate: float | None = None
        estimated_bonus_deduction: float | None = None
        if actual is not None:
            deviation = _relative_deviation(metric_type, threshold, actual)
            if deviation is not None:
                achievement_rate = 1.0 + deviation
                estimated_bonus_deduction = bonus_malus_rate * deviation

        out.append(
            {
                "route_code": r["route_code"],
                "metric_type": metric_type,
                "metric_scope": metric_scope,
                "threshold_value": threshold,
                "bonus_malus_rate": bonus_malus_rate,
                "actual_value": actual,
                "achievement_rate": achievement_rate,
                "estimated_bonus_deduction": estimated_bonus_deduction,
            }
        )
    return out
