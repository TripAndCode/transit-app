"""Per-route "minimum performance standard" bonus/malus simulation, comparing
each configured threshold against the route's on-time rate, its Excess Waiting
Time, its service-delivered rate or its vehicle-km delivered rate.

``route_performance_standards`` (migrations 0041, 0089 and 0094) is a small
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

A ``service_delivered_pct`` target is judged on the route's own rate over the
request's dates (:func:`pipeline.reports.service_delivered.
compute_service_delivered_by_route`), and a ``vehicle_km_delivered_pct`` one
on the same rate, the vehicle-km proxy ``pipeline.reports.supply`` applies,
where the agency's timetable has route shapes. Either is judged only where the
agency's own rate exists, every planned day of the range is split by route,
and the route clears the on-time standard's floor in planned trips and in
observations, and not on a feed that reports zero delay only for the route:
such a feed reports none of the route's trips not run either
(:func:`delivered_reading`).

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
(``on_time_pct``, ``service_delivered_pct``, ``vehicle_km_delivered_pct``):

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
from collections.abc import Collection
from typing import Any

from api.range import RangeCtx
from pipeline.reports.headway_quality import compute_headway_quality
from pipeline.reports.rankings import DEFAULT_EARLY_TOLERANCE_SEC, DEFAULT_LATE_TOLERANCE_SEC, RANKING_MIN_SAMPLES
from pipeline.reports.route_table import compute_route_table
from pipeline.reports.service_delivered import (
    DeliveredMissing,
    RouteDelivered,
    compute_service_delivered_by_route,
)
from pipeline.reports.supply import compute_supply_metrics_by_agency
from pipeline.route_catalog import zero_delay_route_codes

#: The metric types ``route_performance_standards.metric_type``'s CHECK allows.
METRIC_TYPES: tuple[str, ...] = ("ewt_sec", "vehicle_km_delivered_pct", "on_time_pct", "service_delivered_pct")

#: The metrics read on the route's service-delivered rate.
_DELIVERED_METRICS: frozenset[str] = frozenset({"service_delivered_pct", "vehicle_km_delivered_pct"})

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


def on_time_reading(
    share: float | None, observations: int, *, zero_delay: bool
) -> tuple[float | None, dict[str, Any] | None]:
    """A route's on-time figure to judge, or ``None`` with why it is not
    judged: a feed that always reports zero delay reads 100% on time and
    would meet any target, and a share from fewer than the Routes table's
    floor of *observations* judges nothing, however a page with a lower floor
    shows it. The count alone decides, so a share read below the floor is
    never judged and one at it always is."""
    if zero_delay:
        return None, {"reason": "zero_delay_feed", "observations": None, "floor": None}
    if share is None or observations < RANKING_MIN_SAMPLES:
        return None, {"reason": "too_few_observations", "observations": observations, "floor": RANKING_MIN_SAMPLES}
    return share, None


def _not_judged(
    reason: str, *, observations: int | None = None, floor: int | None = None, planned_trips: int | None = None
) -> dict[str, Any]:
    return {"reason": reason, "observations": observations, "floor": floor, "planned_trips": planned_trips}


def delivered_reading(
    agency_missing: DeliveredMissing | None,
    split_known: bool,
    route: RouteDelivered | None,
    *,
    zero_delay: bool,
) -> tuple[float | None, dict[str, Any] | None]:
    """A route's service-delivered share to judge, or ``None`` with why it is
    not judged, in this order: the agency has no rate (*agency_missing*, its
    reason), which a route's rate needs as much; a planned day of the range is
    not split by route, so the route's figure would cover fewer days than the
    range; the route plans fewer trips than the on-time standard's floor in
    observations, :data:`RANKING_MIN_SAMPLES`; its feed reports zero delay
    only; or the feed observed it fewer times than that floor. A route the
    feed barely reports has no trip reported not run, and would read 100%."""
    if agency_missing is not None:
        return None, _not_judged(agency_missing)
    if not split_known:
        return None, _not_judged("route_plan_unknown")
    planned = route.planned_trips if route is not None else 0
    if route is None or planned < RANKING_MIN_SAMPLES:
        return None, _not_judged("too_few_planned_trips", floor=RANKING_MIN_SAMPLES, planned_trips=planned)
    if zero_delay:
        return None, _not_judged("zero_delay_feed")
    if route.observations < RANKING_MIN_SAMPLES:
        return None, _not_judged("too_few_observations", observations=route.observations, floor=RANKING_MIN_SAMPLES)
    return route.service_delivered_pct, None


async def _delivered_by_route(
    agency_id: int, ctx: RangeCtx, conn, codes: Collection[str]
) -> dict[str, tuple[float | None, dict[str, Any] | None]]:
    """:func:`delivered_reading` for each of *codes* over *ctx*'s dates."""
    reading = await compute_service_delivered_by_route(conn, agency_id, ctx.from_date, ctx.to_date)
    no_delay = await zero_delay_route_codes(conn, agency_id, ctx)
    return {
        code: delivered_reading(
            reading.delivered_missing, reading.split_known, reading.routes.get(code), zero_delay=code in no_delay
        )
        for code in codes
    }


async def _has_route_shapes(conn, agency_id: int) -> bool:
    """Whether the agency's current timetable has the planned vehicle-km that
    the vehicle-km delivered rate needs (``pipeline.reports.supply``)."""
    supply = await compute_supply_metrics_by_agency(conn, [agency_id], {})
    return supply[agency_id]["planned_vehicle_km"] is not None


async def _on_time_by_route(
    agency_id: int,
    ctx: RangeCtx,
    conn,
    ch,
    early_tolerance_sec: int | None,
    late_tolerance_sec: int,
    codes: Collection[str],
    *,
    one_route: bool,
) -> dict[str, tuple[float | None, dict[str, Any] | None]]:
    """:func:`on_time_reading` for each of *codes* over *ctx*, from the Routes
    table's own computation: the same call, rounding and on-time window.

    For *one_route*, a route's own page, the table of that route is read once
    at any count and judged against the floor here. Otherwise the table is
    read at its default observation floor, sharing one cached computation
    with the Routes table while it is at that floor and the same routes (with
    sparse routes included the table is computed separately). The codes it
    leaves out are then counted by a second read of those routes alone, which
    runs apart from the first, so a count it finds at the floor is judged
    rather than reported as under it.
    """
    window = {"ch": ch, "early_tolerance_sec": early_tolerance_sec, "late_tolerance_sec": late_tolerance_sec}
    floor = 1 if one_route else RANKING_MIN_SAMPLES
    table = await compute_route_table(agency_id, ctx, conn, min_samples=floor, **window)
    readings: dict[str, tuple[float | None, int]] = {row[0]: (float(row[2]), row[5]) for row in table}
    unread = sorted(set(codes) - readings.keys())
    if unread and not one_route:
        counted = await compute_route_table(
            agency_id, dataclasses.replace(ctx, routes=tuple(unread)), conn, min_samples=1, **window
        )
        readings.update({row[0]: (float(row[2]), row[5]) for row in counted})
    no_delay = await zero_delay_route_codes(conn, agency_id, ctx)
    return {code: on_time_reading(*readings.get(code, (None, 0)), zero_delay=code in no_delay) for code in codes}


async def compute_performance_standards(
    agency_id: int,
    ctx: RangeCtx,
    conn,
    standards: list,
    *,
    route: str | None = None,
    ch=None,
    early_tolerance_sec: int | None = DEFAULT_EARLY_TOLERANCE_SEC,
    late_tolerance_sec: int = DEFAULT_LATE_TOLERANCE_SEC,
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
    An on-time row with no figure carries ``not_judged``, why it has none
    (:func:`on_time_reading`), as does a service-delivered or vehicle-km row
    (:func:`delivered_reading`, or ``no_shapes`` for a vehicle-km row whose
    timetable has no route shapes); every other row carries ``None`` there.
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
    # *standards* is never narrowed by ctx.routes -- read_standards filters
    # only by a named *route* -- so every configured standard the caller
    # asked about is a candidate row. compute_headway_quality,
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

    on_time_by_route: dict[str, tuple[float | None, dict[str, Any] | None]] = {}
    on_time_codes = {r["route_code"] for r in rows if r["metric_type"] == "on_time_pct"}
    if on_time_codes:
        on_time_by_route = await _on_time_by_route(
            agency_id,
            dataclasses.replace(ctx, routes=route_scope),
            conn,
            ch,
            early_tolerance_sec,
            late_tolerance_sec,
            on_time_codes,
            one_route=route is not None,
        )

    delivered_by_route: dict[str, tuple[float | None, dict[str, Any] | None]] = {}
    delivered_codes = {r["route_code"] for r in rows if r["metric_type"] in _DELIVERED_METRICS}
    if delivered_codes:
        delivered_by_route = await _delivered_by_route(
            agency_id, dataclasses.replace(ctx, routes=route_scope, dow="all"), conn, delivered_codes
        )
    has_shapes = any(r["metric_type"] == "vehicle_km_delivered_pct" for r in rows) and await _has_route_shapes(
        conn, agency_id
    )

    out: list[dict[str, Any]] = []
    for r in rows:
        metric_type = r["metric_type"]
        threshold = float(r["threshold_value"])
        bonus_malus_rate = float(r["bonus_malus_rate"])

        not_judged: dict[str, Any] | None = None
        if metric_type == "ewt_sec":
            actual = ewt_by_route.get(r["route_code"])
        elif metric_type == "on_time_pct":
            actual, not_judged = on_time_by_route[r["route_code"]]
        elif metric_type == "vehicle_km_delivered_pct" and not has_shapes:
            actual, not_judged = None, _not_judged("no_shapes")
        else:
            actual, not_judged = delivered_by_route[r["route_code"]]

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
                "threshold_value": threshold,
                "bonus_malus_rate": bonus_malus_rate,
                "actual_value": actual,
                "achievement_rate": achievement_rate,
                "estimated_bonus_deduction": estimated_bonus_deduction,
                "meets": meets_standard(metric_type, threshold, actual),
                "not_judged": not_judged,
            }
        )
    return out
