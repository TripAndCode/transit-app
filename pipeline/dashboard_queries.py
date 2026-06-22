"""SQL aggregations backing the 3 dashboard cards on the Ask tab's empty-thread state.

- delay_heatmap: top-N routes × dimension (DOW or hour-band) avg-delay grid
- anomaly_timeline: 30-day daily avg + std-deviation outliers
- movers: top-N routes by |Δ avg-delay| current-window vs prior-window

All three are all-time/all-service overview cards served entirely from the
precomputed aggregates. They honor date-range + dow + routes; service and
time_band are NOT applied (the aggregates are untyped / have no band column).
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from dataclasses import replace as dc_replace
from datetime import timedelta
from typing import Any

import asyncpg

from api.range import RangeCtx, build_agg_daily_trend_filter
from pipeline import perf
from pipeline.cache import async_lru_cache


@dataclass(frozen=True)
class DelayHeatmap:
    routes: list[dict[str, Any]]  # [{route_code, label}, ...]
    dimensions: list[str]  # column labels (DOW names or hour bands)
    cells: list[list[float | None]]  # routes × dimensions; None where no data
    baseline_min: float  # 1.0 (the on-time threshold) — for diverging color scale


@dataclass(frozen=True)
class AnomalyTimeline:
    series: list[dict[str, Any]]  # [{date, avg_delay}]
    mean: float
    std: float
    anomalies: list[dict[str, Any]]  # [{date, delta_sigma}]


@dataclass(frozen=True)
class Movers:
    rows: list[dict[str, Any]] = field(default_factory=list)


_DOW_LABELS = ["月", "火", "水", "木", "金", "土", "日"]  # 0..6 (Mon..Sun)


@perf.timed("dashboard.heatmap")
@async_lru_cache(maxsize=32, ttl_seconds=300)
async def delay_heatmap(
    conn: asyncpg.Connection,
    *,
    agency_id: int,
    ctx: RangeCtx,
    dimension: str = "dow",
    top_routes: int = 20,
) -> DelayHeatmap:
    """Top-N routes × DOW or hour-band, avg delay (minutes) per cell.

    All-time/all-service overview card served from the aggregates. Honors
    date-range + dow + routes; service and time_band are not applied (the
    aggregates are untyped / have no band column). The hour-band dimension
    buckets by scheduled departure time (all-time, from ``agg_route_hour``).

    ``dimension``:
      - ``"dow"``     → 7 buckets (月..日)
      - ``"hour_band"`` → 4 buckets (朝 5-9, 昼 10-15, 夕 16-20, 夜 21-4)
    """
    if dimension not in ("dow", "hour_band"):
        raise ValueError(f"dimension must be 'dow' or 'hour_band', got {dimension!r}")
    return await _heatmap_from_agg(conn, agency_id, ctx, dimension, top_routes)


async def _build_heatmap(
    conn: asyncpg.Connection,
    agency_id: int,
    route_codes: list[str],
    grid_rows: list[asyncpg.Record],
    n_buckets: int,
    labels_list: list[str],
    normalize: Any,
) -> DelayHeatmap:
    """Shared assembly: route labels + dense routes×buckets cell grid."""
    labels = {
        r["route_code"]: (r["label"] or r["route_code"])
        for r in await conn.fetch(
            # route_code in the aggregates is the digit-only code; static_routes.route_id
            # may carry a trailing "(NNNN)" (Aomori), so derive the code on both sides.
            "SELECT regexp_replace(route_id, '.*\\((\\d+)\\)$', '\\1') AS route_code, route_short_name AS label "
            "FROM static_routes WHERE agency_id = $1 "
            "AND regexp_replace(route_id, '.*\\((\\d+)\\)$', '\\1') = ANY($2::text[])",
            agency_id,
            route_codes,
        )
    }
    by_route: dict[str, dict[int, float | None]] = {rc: {} for rc in route_codes}
    for r in grid_rows:
        b = normalize(r["bucket"])
        by_route[r["route_code"]][b] = float(r["avg_min"]) if r["avg_min"] is not None else None
    cells = [[by_route[rc].get(b) for b in range(n_buckets)] for rc in route_codes]
    routes = [{"route_code": rc, "label": labels.get(rc, rc)} for rc in route_codes]
    return DelayHeatmap(routes=routes, dimensions=labels_list, cells=cells, baseline_min=1.0)


async def _heatmap_from_agg(
    conn: asyncpg.Connection,
    agency_id: int,
    ctx: RangeCtx,
    dimension: str,
    top_routes: int,
) -> DelayHeatmap:
    """DOW from agg_daily_trend (range-aware), hour-band from agg_route_hour
    (all-time, by scheduled departure time). Honors ctx.routes + dow."""
    if dimension == "dow":
        frag, params, n = build_agg_daily_trend_filter(ctx, next_param=2)
        routes_clause = ""
        if ctx.routes:
            routes_clause = f"AND route_code = ANY(${n}::text[])"
            params = [*params, list(ctx.routes)]
            n += 1
        p_top = n
        top_rows = await conn.fetch(
            "SELECT route_code, SUM(samples) AS s FROM agg_daily_trend "
            f"WHERE agency_id = $1 AND {frag} {routes_clause} "
            f"GROUP BY route_code ORDER BY s DESC, route_code LIMIT ${p_top}",
            agency_id,
            *params,
            top_routes,
        )
        route_codes = [r["route_code"] for r in top_rows]
        if not route_codes:
            return DelayHeatmap(routes=[], dimensions=[], cells=[], baseline_min=1.0)
        frag2, params2, n2 = build_agg_daily_trend_filter(ctx, next_param=2)
        rc_param = n2
        grid = await conn.fetch(
            "SELECT route_code, EXTRACT(DOW FROM date::date)::int AS bucket, "
            "SUM(avg_min*samples)/NULLIF(SUM(samples),0) AS avg_min FROM agg_daily_trend "
            f"WHERE agency_id = $1 AND {frag2} AND route_code = ANY(${rc_param}::text[]) "
            "GROUP BY route_code, bucket",
            agency_id,
            *params2,
            route_codes,
        )
        return await _build_heatmap(conn, agency_id, route_codes, grid, 7, _DOW_LABELS, lambda b: (b + 6) % 7)

    # hour_band: all-time, agg_route_hour (by scheduled departure time); honor routes
    routes_clause = ""
    rparams: list = []
    p_top = 2
    if ctx.routes:
        routes_clause = "AND route_code = ANY($2::text[])"
        rparams = [list(ctx.routes)]
        p_top = 3
    top_rows = await conn.fetch(
        "SELECT route_code, SUM(samples) AS s FROM agg_route_hour "
        f"WHERE agency_id = $1 {routes_clause} "
        f"GROUP BY route_code ORDER BY s DESC, route_code LIMIT ${p_top}",
        agency_id,
        *rparams,
        top_routes,
    )
    route_codes = [r["route_code"] for r in top_rows]
    if not route_codes:
        return DelayHeatmap(routes=[], dimensions=[], cells=[], baseline_min=1.0)
    rc_param = 2 + len(rparams)
    grid = await conn.fetch(
        "SELECT route_code, CASE "
        "WHEN EXTRACT(HOUR FROM scheduled_time)::int BETWEEN 5 AND 9 THEN 0 "
        "WHEN EXTRACT(HOUR FROM scheduled_time)::int BETWEEN 10 AND 15 THEN 1 "
        "WHEN EXTRACT(HOUR FROM scheduled_time)::int BETWEEN 16 AND 20 THEN 2 "
        "ELSE 3 END AS bucket, "
        "SUM(avg_min*samples)/NULLIF(SUM(samples),0) AS avg_min FROM agg_route_hour "
        f"WHERE agency_id = $1 {routes_clause} AND route_code = ANY(${rc_param}::text[]) "
        "GROUP BY route_code, bucket",
        agency_id,
        *rparams,
        route_codes,
    )
    return await _build_heatmap(conn, agency_id, route_codes, grid, 4, ["朝", "昼", "夕", "夜"], lambda b: b)


async def _anomalies_series_from_agg(
    conn: asyncpg.Connection,
    agency_id: int,
    ctx: RangeCtx,
) -> list[dict[str, Any]]:
    """Deduped, sample-weighted daily avg from agg_daily_trend. Honors date+dow+routes."""
    frag, params, n = build_agg_daily_trend_filter(ctx, next_param=2)
    routes_clause = ""
    if ctx.routes:
        routes_clause = f"AND route_code = ANY(${n}::text[])"
        params = [*params, list(ctx.routes)]
    rows = await conn.fetch(
        "SELECT date AS d, SUM(avg_min*samples)/NULLIF(SUM(samples),0) AS avg_min "
        f"FROM agg_daily_trend WHERE agency_id = $1 AND {frag} {routes_clause} "
        "GROUP BY date ORDER BY date",
        agency_id,
        *params,
    )
    return [{"date": r["d"], "avg_delay": float(r["avg_min"]) if r["avg_min"] is not None else 0.0} for r in rows]


@perf.timed("dashboard.anomalies")
@async_lru_cache(maxsize=32, ttl_seconds=300)
async def anomaly_timeline(
    conn: asyncpg.Connection,
    *,
    agency_id: int,
    ctx: RangeCtx,
    days: int = 30,
    sigma: float = 2.0,
) -> AnomalyTimeline:
    """Per-day network avg delay (min) over the ctx range + ±sigma outliers.

    All-time/all-service overview card served from agg_daily_trend (deduped,
    sample-weighted across routes). Honors date-range + dow + routes; service
    and time_band are not applied (agg_daily_trend is untyped / has no band
    column).
    """
    series = await _anomalies_series_from_agg(conn, agency_id, ctx)
    if len(series) < 2:
        return AnomalyTimeline(series=series, mean=0.0, std=0.0, anomalies=[])
    vals = [s["avg_delay"] for s in series]
    mean = statistics.fmean(vals)
    std = statistics.pstdev(vals) if len(vals) > 1 else 0.0
    anomalies = []
    if std > 0:
        for s in series:
            ds = (s["avg_delay"] - mean) / std
            if abs(ds) >= sigma:
                anomalies.append({"date": s["date"], "delta_sigma": round(ds, 2)})
    return AnomalyTimeline(series=series, mean=round(mean, 3), std=round(std, 3), anomalies=anomalies)


async def _movers_from_agg(
    conn: asyncpg.Connection,
    agency_id: int,
    ctx: RangeCtx,
    window_days: int,
    top: int,
) -> list[asyncpg.Record]:
    """Read deduped agg_daily_trend. Honors date+dow (+routes)."""
    prv_ctx = dc_replace(
        ctx, from_date=ctx.from_date - timedelta(days=window_days), to_date=ctx.to_date - timedelta(days=window_days)
    )
    cur_frag, cur_params, next_n = build_agg_daily_trend_filter(ctx, next_param=2)
    prv_frag, prv_params, n2 = build_agg_daily_trend_filter(prv_ctx, next_param=next_n)
    # routes filter (agg_daily_trend has route_code; the helper doesn't add it).
    # The same ${n2} placeholder is referenced in BOTH CTEs and bound once
    # positionally — $n2 is the next free slot after cur_params + prv_params.
    routes_clause = ""
    extra_params: list = []
    p_top = n2
    if ctx.routes:
        routes_clause = f"AND route_code = ANY(${n2}::text[])"
        extra_params = [list(ctx.routes)]
        p_top = n2 + 1
    sql = f"""
        WITH cur AS (
            SELECT route_code,
                   SUM(avg_min*samples)/NULLIF(SUM(samples),0) AS avg_min,
                   SUM(samples) AS n
            FROM agg_daily_trend
            WHERE agency_id = $1 AND {cur_frag} {routes_clause}
            GROUP BY route_code
        ),
        prv AS (
            SELECT route_code,
                   SUM(avg_min*samples)/NULLIF(SUM(samples),0) AS avg_min
            FROM agg_daily_trend
            WHERE agency_id = $1 AND {prv_frag} {routes_clause}
            GROUP BY route_code
        )
        SELECT cur.route_code,
               cur.avg_min AS current_avg,
               prv.avg_min AS previous_avg,
               cur.avg_min - COALESCE(prv.avg_min, 0) AS delta,
               cur.n AS samples
        FROM cur LEFT JOIN prv USING (route_code)
        ORDER BY ABS(cur.avg_min - COALESCE(prv.avg_min, 0)) DESC NULLS LAST
        LIMIT ${p_top}
    """
    return await conn.fetch(sql, agency_id, *cur_params, *prv_params, *extra_params, top)


@perf.timed("dashboard.movers")
@async_lru_cache(maxsize=32, ttl_seconds=300)
async def movers(
    conn: asyncpg.Connection,
    *,
    agency_id: int,
    ctx: RangeCtx,
    window_days: int = 7,
    top: int = 10,
) -> Movers:
    """Top N routes by |Δ avg-delay (min)|: current window vs prior equal-length window.

    All-time/all-service overview card served from agg_daily_trend (deduped).
    Honors date-range + dow + routes; service and time_band are not applied
    (agg_daily_trend is untyped / has no band column).
    """
    rows = await _movers_from_agg(conn, agency_id, ctx, window_days, top)
    label_rows = await conn.fetch(
        # Derive the digit-only route_code so labels match the aggregates' keys.
        "SELECT regexp_replace(route_id, '.*\\((\\d+)\\)$', '\\1') AS route_code, route_short_name "
        "FROM static_routes WHERE agency_id = $1",
        agency_id,
    )
    labels = {r["route_code"]: (r["route_short_name"] or r["route_code"]) for r in label_rows}
    out_rows = []
    for r in rows:
        rc = r["route_code"]
        cur_v = float(r["current_avg"]) if r["current_avg"] is not None else None
        prv_v = float(r["previous_avg"]) if r["previous_avg"] is not None else None
        delta = float(r["delta"]) if r["delta"] is not None else 0.0
        pct = (delta / prv_v * 100.0) if prv_v else None
        out_rows.append(
            {
                "route_code": rc,
                "label": labels.get(rc, rc),
                "current_avg": cur_v,
                "previous_avg": prv_v,
                "delta": delta,
                "delta_pct": pct,
                "samples": int(r["samples"]),
            }
        )
    return Movers(rows=out_rows)
