"""The Routes screen's table: one row per route over the window, with its
average delay, on-time rate, share of departures over five minutes late and
early-departure rate, sorted by whichever of the first three the reader picks.
Beside the rows, each route's on-time rate and average delay over its own
trailing window (:func:`compute_route_references`).

Every sort ranks the same routes under one observation floor, so a route keeps
its figures and the table keeps its length however it is sorted. Each figure
is computed as its own report computes it: the average as
:func:`~pipeline.reports.rankings.compute_ranking`'s, the on-time rate as
:func:`~pipeline.reports.rankings.compute_on_time`'s and the over-5-minute
count as :func:`~pipeline.reports.rankings.compute_worst_5min`'s, on the
aggregate path and the live ClickHouse path alike. The five-minute cutoff is
fixed; the on-time rate follows a requested tolerance, and the early-departure
rate counts the departures more than the early tolerance early
(:func:`~pipeline.reports.rankings.early_departure_sec`).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from decimal import Decimal
from typing import Any, Literal

from api.range import RangeCtx, resolve_service_dates
from pipeline import perf
from pipeline.cache import async_lru_cache
from pipeline.histogram import (
    LEGACY_SEVERE_LATE_TOLERANCE_SEC,
    count_early,
    count_on_time,
    exact_window,
    on_time_from_columns,
)
from pipeline.reports.filters import _dedup_cte_ch, _round2, ctx_filing
from pipeline.reports.rankings import (
    DEFAULT_EARLY_TOLERANCE_SEC,
    DEFAULT_LATE_TOLERANCE_SEC,
    MIN_GROUP_SAMPLES,
    _avg_min,
    _pct1,
    _read_dist_scalars,
    _read_dist_with_hist,
    _round1,
    early_departure_sec,
    tolerance_aggregates_serve,
)
from pipeline.reports.reference import (
    Coverage,
    ReferenceReason,
    reference_coverage,
    reference_ctx,
    reference_reason,
    reference_window,
)

# Each sort's row index, and whether the routes needing attention come first
# when that figure is high: a high delay, share or count needs attention, a
# high on-time rate does not.
ROUTE_TABLE_SORTS: dict[str, tuple[int, bool]] = {
    "avg": (1, True),
    "on_time": (2, False),
    "late5_share": (3, True),
    "late5_count": (4, True),
}
DEFAULT_ROUTE_TABLE_SORT = "avg"

RouteTableOrder = Literal["worst", "best"]


@perf.timed("reports.route_table")
@async_lru_cache(maxsize=64, ttl_seconds=300, agency_arg="agency_id")
async def compute_route_table(
    agency_id: int,
    ctx: RangeCtx,
    conn,
    ch=None,
    min_samples: int = MIN_GROUP_SAMPLES,
    early_tolerance_sec: int | None = DEFAULT_EARLY_TOLERANCE_SEC,
    late_tolerance_sec: int = DEFAULT_LATE_TOLERANCE_SEC,
) -> list[tuple]:
    """Every route observed at least ``min_samples`` times in ``ctx``, as
    ``(route_code, avg_min, on_time_pct, late5_pct, late5_count, samples,
    early_pct)``, in no particular order: :func:`sort_route_table` orders them.

    The over-5-minute count reads the exact column analyze() bakes into
    agg_route_daily_dist. An on-time window the exact columns answer, both
    presets among them (:func:`~pipeline.histogram.exact_window`), reads them
    too, with the
    early-departure count summed from the leading histogram buckets; any other
    window merges the routes' delay histograms, as compute_on_time does
    (:func:`~pipeline.histogram.count_on_time`). A time_band filter, or a
    tolerance in the histogram's open end buckets, scans ClickHouse.
    """
    early_sec = early_departure_sec(early_tolerance_sec)
    if not tolerance_aggregates_serve(ctx, early_tolerance_sec, late_tolerance_sec):
        if ch is None:
            raise RuntimeError("compute_route_table's live fallback requires a ClickHouse client")
        return await _route_table_live(
            agency_id, ctx, conn, ch, min_samples, early_tolerance_sec, late_tolerance_sec, early_sec
        )

    high_sec = late_tolerance_sec
    exact = exact_window(early_tolerance_sec, high_sec)
    if exact:
        rows = await _read_dist_scalars(agency_id, ctx, conn, early_sec=early_sec)
    else:
        rows = await _read_dist_with_hist(agency_id, ctx, conn)
    out: list[tuple] = []
    for r in rows:
        samples = r["samples"]
        if samples < min_samples:
            continue
        columns = {"samples": samples, "on_time_count": r["on_time_count"], "late5_count": r["late5_count"]}
        if exact:
            early_count: float = r["early_count"]
            on_time: float = on_time_from_columns(
                early_tolerance_sec, high_sec, early_count=r["early_count"], **columns
            )
        else:
            early_count = count_early(r["hist"], early_sec)
            on_time = count_on_time(r["hist"], early_tolerance_sec, high_sec, **columns)
        out.append(_row(r["route_code"], _avg_min(r["sum_delay_sec"], samples), r, on_time, early_count))
    return out


def _row(route_code: str, avg_min: Decimal, r: Any, on_time_count: float | int, early_count: float) -> tuple:
    samples = r["samples"]
    late5 = r["late5_count"]
    return (
        route_code,
        avg_min,
        _pct1(on_time_count, samples),
        _pct1(late5, samples),
        late5,
        samples,
        _pct1(early_count, samples),
    )


async def _route_table_live(
    agency_id: int,
    ctx: RangeCtx,
    conn,
    ch,
    min_samples: int,
    early_tolerance_sec: int | None,
    late_tolerance_sec: int,
    early_sec: int,
) -> list[tuple]:
    """The table from a live scan, for a time_band the aggregates cannot split
    or a bound in the histogram's open end buckets.

    Counts come back exact and are rounded here, half-up, so the figures agree
    with the aggregate path's. The on-time rate is rounded from the same
    floating-point share compute_on_time's live scans round, so the two never
    differ at a half.
    """
    cte_sql, ch_params = _dedup_cte_ch(ctx, await ctx_filing(conn, ch, agency_id, ctx))
    low_clause = "1" if early_tolerance_sec is None else "dep_delay >= {rt_low:Int32}"
    params = {
        "agency_id": agency_id,
        "rt_high": late_tolerance_sec,
        "rt_low": 0 if early_tolerance_sec is None else -early_tolerance_sec,
        "rt_late5": LEGACY_SEVERE_LATE_TOLERANCE_SEC,
        "rt_early": -early_sec,
        "rt_min": min_samples,
        **ch_params,
    }
    result = await ch.query(
        f"WITH {cte_sql}\n"
        "SELECT route_code,\n"
        "       avg(dep_delay) / 60.0 AS avg_min,\n"
        f"       countIf(dep_delay <= {{rt_high:Int32}} AND {low_clause}) AS on_time_count,\n"
        "       countIf(dep_delay > {rt_late5:Int32}) AS late5_count,\n"
        "       count(*) AS samples,\n"
        "       countIf(dep_delay < {rt_early:Int32}) AS early_count\n"
        "FROM deduped\n"
        "GROUP BY route_code\n"
        "HAVING count(*) >= {rt_min:UInt32}",
        parameters=params,
    )
    return [
        (
            route_code,
            _round2(avg_min),
            _round1(on_time_count * 100.0 / samples),
            _pct1(late5_count, samples),
            late5_count,
            samples,
            _pct1(early_count, samples),
        )
        for route_code, avg_min, on_time_count, late5_count, samples, early_count in result.result_rows
    ]


def sort_route_table(rows: Sequence[tuple], sort: str, order: RouteTableOrder) -> list[tuple]:
    """``rows`` by ``sort``'s figure, the routes needing attention first, or
    last for ``order='best'``. Ties break on the route code ascending either
    way: the rows arrive in whatever order their GROUP BY gave them."""
    index, worst_descending = ROUTE_TABLE_SORTS[sort]
    out = sorted(rows, key=lambda t: t[0])
    out.sort(key=lambda t: t[index], reverse=worst_descending == (order == "worst"))
    return out


@perf.timed("reports.route_references")
@async_lru_cache(maxsize=64, ttl_seconds=300, agency_arg="agency_id")
async def compute_route_references(
    agency_id: int,
    ctx: RangeCtx,
    conn,
    early_tolerance_sec: int | None = DEFAULT_EARLY_TOLERANCE_SEC,
    late_tolerance_sec: int = DEFAULT_LATE_TOLERANCE_SEC,
) -> dict:
    """Every route's on-time rate and average delay over the reference window
    of the period ``ctx`` (:func:`~pipeline.reports.reference.reference_ctx`),
    under ``ctx``'s scope and on-time window, and whether each may stand as the
    route's usual level.

    The figures are :func:`compute_route_table`'s own over the window, at an
    observation floor of one, so each reads exactly as the table would print
    it for those days, from one read of the aggregates for every route. Only a
    scope the aggregates answer has a reference: a time band, or a bound in
    the histogram's open end buckets, would need a live scan of the whole
    window, so it is ``not_aggregated``, decided before anything is read.

    The withholding rules are the shared ones
    (:func:`~pipeline.reports.reference.reference_reason`): the window's
    ``reason`` judges the scope's observations together, each route's its own.
    """
    window = reference_ctx(ctx)
    if not tolerance_aggregates_serve(window, early_tolerance_sec, late_tolerance_sec):
        return {**reference_window(window, Coverage(), "not_aggregated"), "routes": {}}

    window = await resolve_service_dates(window, conn, agency_id)
    _, coverage = await reference_coverage(agency_id, window, conn)
    table = await compute_route_table(
        agency_id,
        window,
        conn,
        min_samples=1,
        early_tolerance_sec=early_tolerance_sec,
        late_tolerance_sec=late_tolerance_sec,
    )
    samples = sum(row[5] for row in table)
    routes = {}
    for code, avg_min, on_time_pct, _late5_pct, _late5_count, route_samples, _early_pct in table:
        reason = reference_reason(route_samples, coverage)
        routes[code] = _route_reference(reason, route_samples, on_time_pct, avg_min)
    return {**reference_window(window, coverage, reference_reason(samples, coverage), samples), "routes": routes}


def _route_reference(
    reason: ReferenceReason | None, samples: int, on_time_pct: Decimal | None = None, avg_min: Decimal | None = None
) -> dict:
    """One route's reference; its figures only without a ``reason``."""
    shown = reason is None
    return {
        "on_time_pct": float(on_time_pct) if shown and on_time_pct is not None else None,
        "avg_min": float(avg_min) if shown and avg_min is not None else None,
        "samples": samples,
        "reason": reason,
    }


def route_references_for(references: dict, codes: Iterable[str]) -> dict:
    """``references`` naming only ``codes``, the routes a response lists, each
    with its reference: one the window never observed has none (``no_data``),
    and under a scope with no aggregate every route shares that reason."""
    routes = references["routes"]
    absent: ReferenceReason = "not_aggregated" if references["reason"] == "not_aggregated" else "no_data"
    return {
        **references,
        "routes": {code: routes[code] if code in routes else _route_reference(absent, 0) for code in codes},
    }
