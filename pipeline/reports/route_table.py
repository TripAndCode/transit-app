"""The Routes screen's table: one row per route over the window, with its
average delay, on-time rate and share of departures over five minutes late,
sorted by whichever of those the reader picks.

Every sort ranks the same routes under one observation floor, so a route keeps
its figures and the table keeps its length however it is sorted. Each figure
is computed as its own report computes it: the average as
:func:`~pipeline.reports.rankings.compute_ranking`'s, the on-time rate as
:func:`~pipeline.reports.rankings.compute_on_time`'s and the over-5-minute
count as :func:`~pipeline.reports.rankings.compute_worst_5min`'s, on the
aggregate path and the live ClickHouse path alike. The five-minute cutoff is
fixed; only the on-time rate follows a requested tolerance.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Any, Literal

from api.range import RangeCtx
from pipeline import perf
from pipeline.cache import async_lru_cache
from pipeline.histogram import (
    LEGACY_ON_TIME_LATE_TOLERANCE_SEC,
    LEGACY_SEVERE_LATE_TOLERANCE_SEC,
    count_in_range,
)
from pipeline.reports.filters import _dedup_cte_ch, _round2
from pipeline.reports.rankings import (
    MIN_GROUP_SAMPLES,
    _avg_min,
    _pct1,
    _read_dist_scalars,
    _read_dist_with_hist,
    _round1,
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
@async_lru_cache(maxsize=64, ttl_seconds=300)
async def compute_route_table(
    agency_id: int,
    ctx: RangeCtx,
    conn,
    ch=None,
    min_samples: int = MIN_GROUP_SAMPLES,
    early_tolerance_sec: int | None = None,
    late_tolerance_sec: int | None = None,
) -> list[tuple]:
    """Every route observed at least ``min_samples`` times in ``ctx``, as
    ``(route_code, avg_min, on_time_pct, late5_pct, late5_count, samples)``,
    in no particular order: :func:`sort_route_table` orders them.

    The default on-time rule and the over-5-minute count read the exact
    columns analyze() bakes into agg_route_daily_dist. A requested on-time
    tolerance reads the merged delay histogram instead, as compute_on_time
    does, and a time_band filter scans ClickHouse.
    """
    if ctx.time_band != "all":
        if ch is None:
            raise RuntimeError("compute_route_table's time_band-filtered live fallback requires a ClickHouse client")
        return await _route_table_live(agency_id, ctx, ch, min_samples, early_tolerance_sec, late_tolerance_sec)

    out: list[tuple] = []
    if early_tolerance_sec is None and late_tolerance_sec is None:
        for r in await _read_dist_scalars(agency_id, ctx, conn):
            samples = r["samples"]
            if samples < min_samples:
                continue
            out.append(_row(r["route_code"], _avg_min(r["sum_delay_sec"], samples), r, r["on_time_count"]))
    else:
        low_sec = None if early_tolerance_sec is None else -early_tolerance_sec
        high_sec = LEGACY_ON_TIME_LATE_TOLERANCE_SEC if late_tolerance_sec is None else late_tolerance_sec
        for r in await _read_dist_with_hist(agency_id, ctx, conn):
            samples = r["samples"]
            if samples < min_samples:
                continue
            on_time = count_in_range(r["hist"], low_sec, high_sec)
            out.append(_row(r["route_code"], _avg_min(r["sum_delay_sec"], samples), r, on_time))
    return out


def _row(route_code: str, avg_min: Decimal, r: Any, on_time_count: float | int) -> tuple:
    samples = r["samples"]
    late5 = r["late5_count"]
    return (route_code, avg_min, _pct1(on_time_count, samples), _pct1(late5, samples), late5, samples)


async def _route_table_live(
    agency_id: int,
    ctx: RangeCtx,
    ch,
    min_samples: int,
    early_tolerance_sec: int | None,
    late_tolerance_sec: int | None,
) -> list[tuple]:
    """The table from a live scan, for a time_band the aggregates cannot split.

    Counts come back exact and are rounded here, half-up, so the figures agree
    with the aggregate path's. The on-time rate is rounded from the same
    floating-point share compute_on_time's live scans round, so the two never
    differ at a half.
    """
    cte_sql, ch_params = _dedup_cte_ch(ctx)
    low_clause = "1" if early_tolerance_sec is None else "dep_delay >= {rt_low:Int32}"
    params = {
        "agency_id": agency_id,
        "rt_high": LEGACY_ON_TIME_LATE_TOLERANCE_SEC if late_tolerance_sec is None else late_tolerance_sec,
        "rt_low": 0 if early_tolerance_sec is None else -early_tolerance_sec,
        "rt_late5": LEGACY_SEVERE_LATE_TOLERANCE_SEC,
        "rt_min": min_samples,
        **ch_params,
    }
    result = await ch.query(
        f"WITH {cte_sql}\n"
        "SELECT route_code,\n"
        "       avg(dep_delay) / 60.0 AS avg_min,\n"
        f"       countIf(dep_delay <= {{rt_high:Int32}} AND {low_clause}) AS on_time_count,\n"
        "       countIf(dep_delay > {rt_late5:Int32}) AS late5_count,\n"
        "       count(*) AS samples\n"
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
        )
        for route_code, avg_min, on_time_count, late5_count, samples in result.result_rows
    ]


def sort_route_table(rows: Sequence[tuple], sort: str, order: RouteTableOrder) -> list[tuple]:
    """``rows`` by ``sort``'s figure, the routes needing attention first, or
    last for ``order='best'``. Ties break on the route code ascending either
    way: the rows arrive in whatever order their GROUP BY gave them."""
    index, worst_descending = ROUTE_TABLE_SORTS[sort]
    out = sorted(rows, key=lambda t: t[0])
    out.sort(key=lambda t: t[index], reverse=worst_descending == (order == "worst"))
    return out
