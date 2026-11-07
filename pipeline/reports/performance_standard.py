"""Per-route "minimum performance standard" bonus/malus simulation, comparing
each configured threshold against the route's on-time rate, its Excess Waiting
Time, or the agency's vehicle-km delivered rate.

``route_performance_standards`` (migrations 0041 and 0089) is a small
policy-input table, written by admins through the agency drawer's standards
editor (``api.routers.admin_agencies.patch_standards``) -- the same convention
as ``ridership_weights`` (migration 0035): a performance standard's threshold
and bonus/malus rate are a negotiated contractual input, not something
derivable from GTFS/GTFS-RT data.

An ``on_time_pct`` target is judged on the Routes table's own on-time figure
for the route (:func:`pipeline.reports.route_table.compute_route_table`), over
the request's scope and under the request's on-time definition, so the
judgement always matches the figure the reader sees beside it. The definition
is not stored with the target; the response names the one it was judged on.

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
(``on_time_pct``, ``vehicle_km_delivered_pct``):

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
``pipeline.reports.service_delivered``). Whether the actual figure meets its
standard (:func:`meets_standard`) needs no ratio, so it is defined at a zero
threshold too.
"""

from __future__ import annotations

import dataclasses
from typing import Any, Literal

from api.range import RangeCtx
from pipeline.reports.headway_quality import compute_headway_quality
from pipeline.reports.rankings import RANKING_MIN_SAMPLES
from pipeline.reports.route_table import compute_route_table
from pipeline.reports.service_delivered import compute_service_delivered_by_agency
from pipeline.reports.supply import compute_supply_metrics_by_agency
from pipeline.route_catalog import zero_delay_route_codes

#: The metric types ``route_performance_standards.metric_type``'s CHECK allows.
METRIC_TYPES: tuple[str, ...] = ("ewt_sec", "vehicle_km_delivered_pct", "on_time_pct")

# Metrics for which a SMALLER actual value is better performance. Any
# metric_type not listed here is treated as higher-is-better.
_LOWER_IS_BETTER: frozenset[str] = frozenset({"ewt_sec"})

_STANDARDS_SQL = """
    SELECT route_code, metric_type, threshold_value, bonus_malus_rate
    FROM route_performance_standards
    WHERE agency_id = $1 AND ($2::text IS NULL OR route_code = $2)
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


def meets_standard(metric_type: str, threshold: float, actual: float | None) -> bool | None:
    """Whether *actual* is at least as good as *threshold* for *metric_type*,
    or ``None`` when there is no figure to judge."""
    if actual is None:
        return None
    return actual <= threshold if metric_type in _LOWER_IS_BETTER else actual >= threshold


async def read_standards(conn, agency_id: int, route: str | None = None) -> list:
    """*agency_id*'s configured standards, or only *route*'s."""
    return await conn.fetch(_STANDARDS_SQL, agency_id, route)


async def _on_time_by_route(
    agency_id: int,
    ctx: RangeCtx,
    conn,
    ch,
    early_tolerance_sec: int | None,
    late_tolerance_sec: int | None,
) -> dict[str, float]:
    """Each route of *ctx*'s on-time share as the Routes table shows it at its
    default observation floor: the same call, floor and rounding. While the
    table is at that floor and the same routes, the two share one cached
    computation; with sparse routes included, the table is computed
    separately and this keeps the default floor.

    A route the table leaves out for a feed that always reports zero delay
    has no figure here either: it reads 100% on time and would meet any
    target. A route under the table's observation floor has none, as a
    figure resting on so few departures judges nothing.
    """
    table = await compute_route_table(
        agency_id,
        ctx,
        conn,
        ch=ch,
        min_samples=RANKING_MIN_SAMPLES,
        early_tolerance_sec=early_tolerance_sec,
        late_tolerance_sec=late_tolerance_sec,
    )
    no_delay = await zero_delay_route_codes(conn, agency_id, ctx)
    return {row[0]: float(row[2]) for row in table if row[0] not in no_delay}


async def compute_performance_standards(
    agency_id: int,
    ctx: RangeCtx,
    conn,
    standards: list,
    *,
    route: str | None = None,
    ch=None,
    early_tolerance_sec: int | None = None,
    late_tolerance_sec: int | None = None,
) -> list[dict[str, Any]]:
    """Every row of *standards* (:func:`read_standards` for *agency_id*, and
    for *route* when one is named), joined against the current actual value
    of its ``metric_type`` over *ctx*'s date range, with the resulting
    achievement rate and estimated bonus/deduction.

    An agency with zero configured rows returns an empty list -- "not
    configured", never a fabricated standard. A configured row whose actual
    value can't be resolved for this range (e.g. an ``ewt_sec`` standard on
    a route that isn't classified high-frequency, or that has no in-range
    headway data yet) is still returned, with ``actual_value``,
    ``achievement_rate`` and ``estimated_bonus_deduction`` all ``None`` --
    "insufficient data", not a silent zero.

    ``on_time_pct`` rows read *ctx*'s days, time band and service and the
    on-time window *early_tolerance_sec*/*late_tolerance_sec* name, as the
    Routes table does (:func:`_on_time_by_route`; *ch* serves its live scan).
    The other metric types read *ctx*'s dates only.

    ``ctx.routes`` (the page-level route filter shared with every other
    Analysis-tab panel) never changes WHICH configured rows come back, nor
    whether a given row's actual value resolves -- every configured route
    is always fully evaluated. *route* is different: a route's own page
    names the one route it judges, and every figure is then read for that
    route alone rather than for the whole agency.
    """
    rows = standards
    if not rows:
        return []
    route_scope: tuple[str, ...] = () if route is None else (route,)

    # Resolve every actual value this agency's configured rows could need,
    # up front -- one pass per metric_type family rather than a query per
    # row, mirroring compute_headway_quality/compute_supply_metrics_by_agency's
    # own per-agency batching.
    #
    # _STANDARDS_SQL above is deliberately unfiltered by ctx.routes -- every
    # configured standard is always a candidate row. compute_headway_quality,
    # however, applies ctx.routes as a page-level filter (it's shared with
    # every other Analysis-tab panel). Resolving ewt_sec with the caller's
    # ctx as-is would silently blank out a configured route's actual value
    # to None whenever the page's route filter happens to exclude it --
    # indistinguishable from genuinely insufficient data. The page's weekday
    # filter would do the same, and an EWT standard reads dates only. A copy
    # of ctx free of both keeps a configured standard's actual value resolved
    # regardless of the page filter. The on-time rows drop the route filter
    # alone, since they follow the rest of the scope as the table does. A
    # named *route* replaces the filter in both, so a route's own page reads
    # that route alone.
    ewt_by_route: dict[str, float | None] = {}
    if any(r["metric_type"] == "ewt_sec" for r in rows):
        unfiltered_ctx = dataclasses.replace(ctx, routes=route_scope, dow="all")
        quality_rows = await compute_headway_quality(agency_id, unfiltered_ctx, conn)
        ewt_by_route = {q["route_code"]: q["ewt_sec"] for q in quality_rows}

    on_time_by_route: dict[str, float] = {}
    if any(r["metric_type"] == "on_time_pct" for r in rows):
        on_time_by_route = await _on_time_by_route(
            agency_id, dataclasses.replace(ctx, routes=route_scope), conn, ch, early_tolerance_sec, late_tolerance_sec
        )

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

        metric_scope: Literal["route", "agency"] = "route"
        if metric_type == "ewt_sec":
            actual = ewt_by_route.get(r["route_code"])
        elif metric_type == "on_time_pct":
            actual = on_time_by_route.get(r["route_code"])
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
                "meets": meets_standard(metric_type, threshold, actual),
            }
        )
    return out
