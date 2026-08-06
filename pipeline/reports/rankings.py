"""The seven report-tab compute_* functions (ranking, on-time, trend, etc.)."""

from __future__ import annotations

from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal

from api.range import RangeCtx, build_updates_filter_ch
from pipeline import perf
from pipeline.cache import async_lru_cache
from pipeline.histogram import percentile_from_hist
from pipeline.reports.filters import _agg_filter, _ch_rows, _dedup_cte_ch, _dist_filter

# Reports read the precomputed per-day distribution (agg_route_daily_dist) and
# sum across the range. The aggregate has no hour-of-day column, so a time_band
# filter can't be served from it — those queries fall back to the live scan.
_MIN = Decimal("0.01")  # 2-dp minutes, matching the live ROUND(..., 2)


def _service_or_none(service_type: str) -> str | None:
    """Map the '' NOT-NULL PK sentinel back to None (NULL-service routes)."""
    return service_type or None


def _avg_min(sum_delay_sec: int, samples: int) -> Decimal:
    """Mean delay in minutes, 2 dp — matches live ROUND(AVG(dep_delay)/60, 2).

    ROUND_HALF_UP mirrors Postgres ROUND (half away from zero); Decimal's
    default ROUND_HALF_EVEN would diverge at exact-half 2-dp boundaries.
    """
    return (Decimal(sum_delay_sec) / samples / 60).quantize(_MIN, rounding=ROUND_HALF_UP)


def _sec_to_min(sec: float | None) -> Decimal | None:
    """Seconds → 2-dp minutes, or None for an empty histogram."""
    return None if sec is None else (Decimal(sec) / 60).quantize(_MIN, rounding=ROUND_HALF_UP)


async def _read_dist_scalars(agency_id: int, ctx: RangeCtx, conn) -> list:
    """Range-scan agg_route_daily_dist, summing the exact per-route scalars.

    Used by on_time / worst_5min (no percentiles needed). DOW/service/route
    filters apply; time_band is the caller's responsibility (see module note).
    """
    where, params, _ = _dist_filter(ctx, next_param=2)
    sql = (
        "SELECT route_code, service_type,\n"
        "       SUM(samples) AS samples,\n"
        "       SUM(sum_delay_sec) AS sum_delay_sec,\n"
        "       SUM(on_time_count) AS on_time_count,\n"
        "       SUM(late5_count) AS late5_count\n"
        "FROM agg_route_daily_dist\n"
        f"WHERE agency_id = $1 AND {where}\n"
        "GROUP BY route_code, service_type"
    )
    return await conn.fetch(sql, agency_id, *params)


async def _read_dist_with_hist(agency_id: int, ctx: RangeCtx, conn) -> list:
    """Range-scan agg_route_daily_dist, summing scalars AND merging histograms.

    The histograms are summed element-wise (unnest WITH ORDINALITY → regroup →
    array_agg) so p50/p90 can be interpolated from the merged buckets in Python.
    """
    where, params, _ = _dist_filter(ctx, next_param=2)
    sql = (
        "WITH ranged AS (\n"
        "    SELECT route_code, service_type, samples, sum_delay_sec, hist\n"
        "    FROM agg_route_daily_dist\n"
        f"    WHERE agency_id = $1 AND {where}\n"
        "),\n"
        "merged_hist AS (\n"
        "    SELECT route_code, service_type, i, SUM(h) AS c\n"
        "    FROM ranged, unnest(hist) WITH ORDINALITY u(h, i)\n"
        "    GROUP BY route_code, service_type, i\n"
        "),\n"
        "hists AS (\n"
        "    SELECT route_code, service_type, array_agg(c ORDER BY i) AS hist\n"
        "    FROM merged_hist GROUP BY route_code, service_type\n"
        "),\n"
        "scalars AS (\n"
        "    SELECT route_code, service_type,\n"
        "           SUM(samples) AS samples, SUM(sum_delay_sec) AS sum_delay_sec\n"
        "    FROM ranged GROUP BY route_code, service_type\n"
        ")\n"
        "SELECT s.route_code, s.service_type, s.samples, s.sum_delay_sec, h.hist\n"
        "FROM scalars s JOIN hists h USING (route_code, service_type)"
    )
    return await conn.fetch(sql, agency_id, *params)


@perf.timed("reports.ranking")
@async_lru_cache(maxsize=64, ttl_seconds=300)
async def compute_ranking(
    agency_id: int,
    ctx: RangeCtx,
    conn,
    ch=None,
    sort_order: str = "desc",
    limit: int = 100,
) -> list[tuple]:
    """Routes ranked by average delay over ctx. ``sort_order='asc'`` → best first.

    Reads agg_route_daily_dist: avg/samples are exact; p50/p90 are interpolated
    from the merged delay histogram (approximate within one bucket — fine for
    ranking). A time_band filter falls back to the live scan (ClickHouse).
    """
    if ctx.time_band != "all":
        return await _ranking_live(agency_id, ctx, conn, ch, sort_order, limit)

    rows = await _read_dist_with_hist(agency_id, ctx, conn)
    out: list[tuple] = []
    for r in rows:
        samples = r["samples"]
        if samples <= 20:  # mirror live HAVING COUNT(*) > 20
            continue
        out.append(
            (
                r["route_code"],
                _service_or_none(r["service_type"]),
                _avg_min(r["sum_delay_sec"], samples),
                _sec_to_min(percentile_from_hist(r["hist"], 0.5)),
                _sec_to_min(percentile_from_hist(r["hist"], 0.9)),
                samples,
            )
        )
    # avg_min is element 2; None never occurs (samples > 20), so plain sort.
    out.sort(key=lambda t: t[2], reverse=sort_order.lower() == "desc")
    return out[:limit]


async def _ranking_live(agency_id: int, ctx: RangeCtx, conn, ch, sort_order: str, limit: int) -> list[tuple]:
    """Live raw-scan ranking — fallback for time_band-filtered queries.

    p50/p90 use ClickHouse's `quantileExact` aggregate instead of the
    Postgres version's `PERCENT_RANK() OVER (...)` window + boundary-row
    pick — ClickHouse's window-function support doesn't cover
    `PERCENT_RANK`, and `quantileExact` is the more idiomatic ClickHouse way
    to get an exact per-group percentile directly, with no window/CTE
    indirection needed. Every group here has > 20 rows (the HAVING gate),
    so `avg`/`quantileExact` are never NULL/NaN — no empty-input guard needed.
    """
    cte_sql, ch_params = _dedup_cte_ch(ctx)
    order = "DESC" if sort_order.lower() == "desc" else "ASC"
    result = await ch.query(
        f"WITH {cte_sql}\n"
        "SELECT route_code, service_type,\n"
        "       round(avg(dep_delay) / 60.0, 2) AS avg_min,\n"
        "       round(quantileExact(0.5)(dep_delay) / 60.0, 2) AS p50_min,\n"
        "       round(quantileExact(0.9)(dep_delay) / 60.0, 2) AS p90_min,\n"
        "       count(*) AS samples\n"
        "FROM deduped\n"
        "GROUP BY route_code, service_type\n"
        "HAVING count(*) > 20\n"
        f"ORDER BY avg_min {order}\n"
        "LIMIT {rk_limit:UInt32}",
        parameters={"agency_id": agency_id, "rk_limit": limit, **ch_params},
    )
    return [tuple(r) for r in result.result_rows]


@perf.timed("reports.on_time")
@async_lru_cache(maxsize=64, ttl_seconds=300)
async def compute_on_time(
    agency_id: int,
    ctx: RangeCtx,
    conn,
    ch=None,
    threshold_sec: int = 60,
    limit: int = 100,
    sort_order: str = "desc",
) -> list[tuple]:
    """On-time percentage per route-service. ``threshold_sec`` is the cutoff.

    ``sort_order='desc'`` returns best on-time routes first (highest %);
    ``sort_order='asc'`` returns worst routes first (lowest %) for BUG-3.

    Reads agg_route_daily_dist (exact). The on-time threshold is baked into
    ``on_time_count`` (<=60s) at analyze time, so a non-default ``threshold_sec``
    or a time_band filter falls back to the live scan (ClickHouse).
    """
    if ctx.time_band != "all" or threshold_sec != 60:
        return await _on_time_live(agency_id, ctx, conn, ch, threshold_sec, limit, sort_order)

    rows = await _read_dist_scalars(agency_id, ctx, conn)
    out: list[tuple] = []
    for r in rows:
        samples = r["samples"]
        if samples <= 20:
            continue
        on_time_pct = (Decimal(r["on_time_count"]) * 100 / samples).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        out.append(
            (
                r["route_code"],
                _service_or_none(r["service_type"]),
                on_time_pct,
                _avg_min(r["sum_delay_sec"], samples),
                samples,
            )
        )
    out.sort(key=lambda t: t[2], reverse=sort_order.lower() == "desc")
    return out[:limit]


async def _on_time_live(
    agency_id: int, ctx: RangeCtx, conn, ch, threshold_sec: int, limit: int, sort_order: str
) -> list[tuple]:
    """Live raw-scan on-time — fallback for time_band / custom-threshold queries.

    Every group here has > 20 rows (the HAVING gate), so `avg`/`sum` are
    never NULL/NaN — no empty-input guard needed.
    """
    cte_sql, ch_params = _dedup_cte_ch(ctx)
    order = "DESC" if sort_order.lower() == "desc" else "ASC"
    result = await ch.query(
        f"WITH {cte_sql}\n"
        "SELECT route_code, service_type,\n"
        "       round(sum(CASE WHEN dep_delay <= "
        f"{threshold_sec}"
        " THEN 1.0 ELSE 0 END) * 100.0 / count(*), 1) AS on_time_pct,\n"
        "       round(avg(dep_delay) / 60.0, 2) AS avg_min,\n"
        "       count(*) AS samples\n"
        "FROM deduped\n"
        "GROUP BY route_code, service_type\n"
        "HAVING count(*) > 20\n"
        f"ORDER BY on_time_pct {order}\n"
        "LIMIT {ot_limit:UInt32}",
        parameters={"agency_id": agency_id, "ot_limit": limit, **ch_params},
    )
    return [tuple(r) for r in result.result_rows]


@perf.timed("reports.worst_5min")
@async_lru_cache(maxsize=64, ttl_seconds=300)
async def compute_worst_5min(
    agency_id: int,
    ctx: RangeCtx,
    conn,
    ch=None,
    limit: int = 100,
) -> list[tuple]:
    """Routes ranked by count of >5min late observations.

    Reads agg_route_daily_dist (``late5_count`` is exact, >300s baked in at
    analyze time). A time_band filter falls back to the live scan (ClickHouse).
    """
    if ctx.time_band != "all":
        return await _worst_5min_live(agency_id, ctx, conn, ch, limit)

    rows = await _read_dist_scalars(agency_id, ctx, conn)
    out: list[tuple] = []
    for r in rows:
        late5 = r["late5_count"]
        if late5 <= 0:  # mirror live HAVING SUM(...) > 0
            continue
        out.append(
            (
                r["route_code"],
                _service_or_none(r["service_type"]),
                late5,
                _avg_min(r["sum_delay_sec"], r["samples"]),
                r["samples"],
            )
        )
    out.sort(key=lambda t: t[2], reverse=True)
    return out[:limit]


async def _worst_5min_live(agency_id: int, ctx: RangeCtx, conn, ch, limit: int) -> list[tuple]:
    """Live raw-scan worst-5min — fallback for time_band-filtered queries."""
    cte_sql, ch_params = _dedup_cte_ch(ctx)
    result = await ch.query(
        f"WITH {cte_sql}\n"
        "SELECT route_code, service_type,\n"
        "       sum(CASE WHEN dep_delay > 300 THEN 1 ELSE 0 END) AS late5_count,\n"
        "       round(avg(dep_delay) / 60.0, 2) AS avg_min,\n"
        "       count(*) AS samples\n"
        "FROM deduped\n"
        "GROUP BY route_code, service_type\n"
        "HAVING sum(CASE WHEN dep_delay > 300 THEN 1 ELSE 0 END) > 0\n"
        "ORDER BY late5_count DESC\n"
        "LIMIT {w5_limit:UInt32}",
        parameters={"agency_id": agency_id, "w5_limit": limit, **ch_params},
    )
    return [tuple(r) for r in result.result_rows]


@perf.timed("reports.dow_ranking")
@async_lru_cache(maxsize=64, ttl_seconds=300)
async def compute_dow_ranking(
    agency_id: int,
    ctx: RangeCtx,
    conn,
    dow_group: str,  # 'weekday' or 'weekend'
    limit: int = 100,
    ch=None,
) -> list[tuple]:
    """Worst-delay routes restricted to weekday or weekend observations.

    Drops the user's ``service`` filter on purpose — pairing 'weekend' DOW
    with service_type='平日' yields zero rows in the Aomori dataset because
    weekday-schedule trips don't run on weekends. ``routes`` is preserved
    so a user filtering to specific routes keeps that filter.
    """
    overridden = RangeCtx(
        from_date=ctx.from_date,
        to_date=ctx.to_date,
        dow=dow_group,  # type: ignore[arg-type]
        time_band=ctx.time_band,
        service="all",
        routes=ctx.routes,
    )
    label = "週末" if dow_group == "weekend" else "平日"
    if ctx.time_band != "all":
        return await _dow_ranking_live(agency_id, overridden, conn, ch, label, limit)

    # agg_daily_trend filtered to the weekday/weekend dates, sample-weighted.
    where, params, n = _agg_filter(overridden, next_param=2)
    sql = (
        # NULLIF maps the '' NULL-service sentinel back to None, matching the live path.
        f"SELECT route_code, NULLIF(service_type, '') AS service_type, '{label}' AS dow,\n"
        "       ROUND((SUM(avg_min * samples) / NULLIF(SUM(samples), 0))::numeric, 2) AS avg_min,\n"
        "       SUM(samples)::int AS samples\n"
        "FROM agg_daily_trend\n"
        f"WHERE agency_id = $1 AND {where}\n"
        "GROUP BY route_code, service_type\n"
        "HAVING SUM(samples) > 10\n"
        "ORDER BY avg_min DESC NULLS LAST\n"
        f"LIMIT ${n}"
    )
    rows = await conn.fetch(sql, agency_id, *params, limit)
    return [tuple(r) for r in rows]


async def _dow_ranking_live(agency_id: int, overridden: RangeCtx, conn, ch, label: str, limit: int) -> list[tuple]:
    """Live raw-scan dow-ranking — fallback for time_band-filtered queries."""
    cte_sql, ch_params = _dedup_cte_ch(overridden)
    result = await ch.query(
        f"WITH {cte_sql}\n"
        f"SELECT route_code, service_type, '{label}' AS dow,\n"
        "       round(avg(dep_delay) / 60.0, 2) AS avg_min,\n"
        "       count(*) AS samples\n"
        "FROM deduped\n"
        "GROUP BY route_code, service_type\n"
        "HAVING count(*) > 10\n"
        "ORDER BY avg_min DESC\n"
        "LIMIT {dr_limit:UInt32}",
        parameters={"agency_id": agency_id, "dr_limit": limit, **ch_params},
    )
    return [tuple(r) for r in result.result_rows]


@perf.timed("reports.compare_ranking")
@async_lru_cache(maxsize=64, ttl_seconds=300)
async def compute_compare_ranking(
    agency_id: int,
    ctx: RangeCtx,
    conn,
    ch,
    limit: int = 100,
) -> list[tuple]:
    """Per-route weekday-vs-weekend delay difference, sorted by absolute delta.

    Drops the user's ``service`` filter (same reason as compute_dow_ranking)
    but preserves ``routes`` so route-restricted comparisons work.
    """
    if ctx.time_band != "all":
        return await _compare_ranking_live(agency_id, ctx, ch, limit)

    # Both DOW sides from one agg_daily_trend pass (dow split via FILTER), summed
    # across service/date per route. dow='all' so _agg_filter emits no DOW clause.
    agg_ctx = RangeCtx(
        from_date=ctx.from_date,
        to_date=ctx.to_date,
        dow="all",
        time_band=ctx.time_band,
        service="all",
        routes=ctx.routes,
    )
    where, params, n = _agg_filter(agg_ctx, next_param=2)
    wd = "EXTRACT(ISODOW FROM date::date) BETWEEN 1 AND 5"
    we = "EXTRACT(ISODOW FROM date::date) IN (6, 7)"
    sql = (
        "WITH per_route AS (\n"
        "    SELECT route_code,\n"
        f"           SUM(avg_min * samples) FILTER (WHERE {wd})\n"
        f"             / NULLIF(SUM(samples) FILTER (WHERE {wd}), 0) AS wd_avg,\n"
        f"           SUM(samples) FILTER (WHERE {wd}) AS wd_n,\n"
        f"           SUM(avg_min * samples) FILTER (WHERE {we})\n"
        f"             / NULLIF(SUM(samples) FILTER (WHERE {we}), 0) AS we_avg,\n"
        f"           SUM(samples) FILTER (WHERE {we}) AS we_n\n"
        "    FROM agg_daily_trend\n"
        f"    WHERE agency_id = $1 AND {where}\n"
        "    GROUP BY route_code\n"
        ")\n"
        "SELECT route_code,\n"
        "       ROUND(wd_avg::numeric, 2) AS heijitsu,\n"
        "       ROUND(we_avg::numeric, 2) AS kyujitsu,\n"
        "       ROUND(ABS(wd_avg - we_avg)::numeric, 2) AS abs_delta,\n"
        "       ROUND((we_avg - wd_avg)::numeric, 2) AS signed_delta\n"
        "FROM per_route\n"
        "WHERE wd_n > 10 AND we_n > 10\n"
        "ORDER BY ABS(wd_avg - we_avg) DESC\n"
        f"LIMIT ${n}"
    )
    rows = await conn.fetch(sql, agency_id, *params, limit)
    return [tuple(r) for r in rows]


async def _route_avg_by_dow_ch(agency_id: int, day_ctx: RangeCtx, ch) -> dict[str, tuple[float, int]]:
    """Per-route (avg_min, n) from ClickHouse `updates`, deduped on a narrower
    key than `build_dedup_ch_sql` (no service_type/scheduled_time — the
    weekday-vs-weekend rollup doesn't need them; assumes (trip_id, date)
    determines service_type in clean data, same assumption the original
    Postgres wd_dedup/we_dedup CTEs made). Routes with <=10 deduped
    observations are dropped, mirroring the original SQL's HAVING COUNT(*) > 10.
    """
    where_frag, params = build_updates_filter_ch(day_ctx)
    result = await ch.query(
        f"""
        SELECT route_code, dep_delay
        FROM updates
        WHERE agency_id = {{agency_id:UInt16}} AND dep_delay IS NOT NULL AND {where_frag}
        ORDER BY captured_at DESC, file_name DESC
        LIMIT 1 BY route_code, trip_id, toDate(captured_at, 'Asia/Tokyo'), stop_sequence
        """,
        parameters={"agency_id": agency_id, **params},
    )
    delays_by_route: dict[str, list[int]] = defaultdict(list)
    for route_code, dep_delay in result.result_rows:
        delays_by_route[route_code].append(dep_delay)
    return {
        route_code: (sum(delays) / len(delays) / 60.0, len(delays))
        for route_code, delays in delays_by_route.items()
        if len(delays) > 10
    }


async def _compare_ranking_live(agency_id: int, ctx: RangeCtx, ch, limit: int) -> list[tuple]:
    """Live raw-scan weekday-vs-weekend compare — fallback for time_band queries."""
    weekday_ctx = RangeCtx(
        from_date=ctx.from_date,
        to_date=ctx.to_date,
        dow="weekday",
        time_band=ctx.time_band,
        service="all",
        routes=ctx.routes,
    )
    weekend_ctx = RangeCtx(
        from_date=ctx.from_date,
        to_date=ctx.to_date,
        dow="weekend",
        time_band=ctx.time_band,
        service="all",
        routes=ctx.routes,
    )
    wd_avg = await _route_avg_by_dow_ch(agency_id, weekday_ctx, ch)
    we_avg = await _route_avg_by_dow_ch(agency_id, weekend_ctx, ch)

    def _round2(x: float) -> Decimal:
        return Decimal(str(x)).quantize(_MIN, rounding=ROUND_HALF_UP)

    out: list[tuple] = []
    for route_code, (wd_mean, _wd_n) in wd_avg.items():
        if route_code not in we_avg:
            continue
        we_mean, _we_n = we_avg[route_code]
        out.append(
            (
                route_code,
                _round2(wd_mean),
                _round2(we_mean),
                _round2(abs(wd_mean - we_mean)),
                _round2(we_mean - wd_mean),
            )
        )
    # Sort by the UNROUNDED delta (matches the original SQL's ORDER BY
    # ABS(wd.avg_min - we.avg_min), computed before the display-only ROUND).
    out.sort(key=lambda r: -abs(wd_avg[r[0]][0] - we_avg[r[0]][0]))
    return out[:limit]


# ---------------------------------------------------------------------------
# Trend (new endpoint, not just a report row)
# ---------------------------------------------------------------------------


@perf.timed("reports.hourly_heatmap")
@async_lru_cache(maxsize=16, ttl_seconds=300)
async def compute_hourly_heatmap(
    agency_id: int,
    ctx: RangeCtx,
    conn,
    ch=None,
) -> list[dict]:
    """Hour-of-day × date cells for the granular trend view.

    Returns ``[ { date, hour, avg_min, samples } ]`` filtered by the same
    ctx the rest of the trend uses. Hour is extracted from
    ``scheduled_time`` (a TIME column post migration 0011); cells with too
    few samples (<3) are dropped to keep the rendering signal-strong.
    """
    # agg_hour_daily is already (date, hour, avg_min, samples) across all routes —
    # a direct read. It has no service/route/hour-band dimension, so any of those
    # filters fall back to the live dedup scan (ClickHouse). DOW (on the date
    # column) is fine.
    if ctx.time_band == "all" and ctx.service == "all" and not ctx.routes:
        where, params, _ = _dist_filter(ctx, next_param=2)
        sql = (
            "SELECT date, hour, avg_min, samples\n"
            "FROM agg_hour_daily\n"
            f"WHERE agency_id = $1 AND {where} AND samples >= 3\n"
            "ORDER BY date, hour"
        )
        rows = await conn.fetch(sql, agency_id, *params)
    else:
        # scheduled_time is a zero-padded 'HH:MM:SS' String in ClickHouse
        # (not a native TIME column) — see api.range.time_band_clause_ch's
        # docstring — so the hour is read off the first two characters
        # rather than EXTRACT(). Every group here has >= 3 rows (the HAVING
        # gate), so avg() is never NaN.
        cte_sql, ch_params = _dedup_cte_ch(ctx)
        hour_expr = "toUInt8(substring(scheduled_time, 1, 2))"
        result = await ch.query(
            f"WITH {cte_sql}\n"
            f"SELECT date, {hour_expr} AS hour,\n"
            "       round(avg(dep_delay) / 60.0, 2) AS avg_min,\n"
            "       count(*) AS samples\n"
            "FROM deduped\n"
            "WHERE scheduled_time IS NOT NULL\n"
            f"GROUP BY date, {hour_expr}\n"
            "HAVING count(*) >= 3\n"
            "ORDER BY date, hour",
            parameters={"agency_id": agency_id, **ch_params},
        )
        rows = _ch_rows(result)
    return [
        {
            "date": r["date"].isoformat(),
            "hour": int(r["hour"]),
            "avg_min": float(r["avg_min"]) if r["avg_min"] is not None else None,
            "samples": r["samples"],
        }
        for r in rows
    ]


@perf.timed("reports.trend_series")
@async_lru_cache(maxsize=32, ttl_seconds=300)
async def compute_trend_series(
    agency_id: int,
    ctx: RangeCtx,
    conn,
    top_offenders: int = 3,
    granularity: str = "day",
    ch=None,
) -> dict:
    """Bucketed series + per-bucket worst-route attribution for the Trend chart.

    ``granularity`` controls the time bucket: ``'day'`` (default), ``'week'``,
    or ``'month'``. Returns
    ``{ days: [{ date, avg_min, samples, top_offenders: [...] }] }``
    where ``date`` is the bucket start date (ISO string).
    """
    # Map granularity to a date_trunc unit; fall back to 'day' for unknown values.
    _TRUNC = {"day": "day", "week": "week", "month": "month"}
    trunc_unit = _TRUNC.get(granularity, "day")

    if ctx.time_band == "all":
        # Sum agg_daily_trend (already per date/route/service) into time buckets.
        where, params, _ = _agg_filter(ctx, next_param=2)
        sql = (
            f"SELECT date_trunc('{trunc_unit}', date::date::timestamp)::date AS bucket,\n"
            "       route_code, NULLIF(service_type, '') AS service_type,\n"
            "       ROUND((SUM(avg_min * samples) / NULLIF(SUM(samples), 0))::numeric, 2) AS avg_min,\n"
            "       SUM(samples)::int AS samples\n"
            "FROM agg_daily_trend\n"
            f"WHERE agency_id = $1 AND {where}\n"
            "GROUP BY bucket, route_code, service_type\n"
            "HAVING SUM(samples) > 5"
        )
        per_day = await conn.fetch(sql, agency_id, *params)
    else:
        # time_band filter needs the hour-of-day, only on raw updates
        # (ClickHouse). Every group here has > 5 rows (the HAVING gate), so
        # avg() is never NaN.
        _CH_BUCKET_EXPR = {"day": "date", "week": "toStartOfWeek(date, 1)", "month": "toStartOfMonth(date)"}
        bucket_expr = _CH_BUCKET_EXPR.get(granularity, "date")
        cte_sql, ch_params = _dedup_cte_ch(ctx)
        result = await ch.query(
            f"WITH {cte_sql}\n"
            f"SELECT {bucket_expr} AS bucket,\n"
            "       route_code, service_type,\n"
            "       round(avg(dep_delay) / 60.0, 2) AS avg_min,\n"
            "       count(*) AS samples\n"
            "FROM deduped\n"
            "GROUP BY bucket, route_code, service_type\n"
            "HAVING count(*) > 5",
            parameters={"agency_id": agency_id, **ch_params},
        )
        per_day = _ch_rows(result)

    by_date_samples: dict = {}
    by_date_weighted: dict = {}
    by_date: dict = {}
    for r in per_day:
        # SQL now aliases the time-bucketed column as "bucket".
        d = r["bucket"]
        avg = float(r["avg_min"]) if r["avg_min"] is not None else None
        n = r["samples"]
        by_date_samples[d] = by_date_samples.get(d, 0) + n
        if avg is not None:
            by_date_weighted[d] = by_date_weighted.get(d, 0.0) + avg * n
        by_date.setdefault(d, []).append(
            {
                "route_code": r["route_code"],
                "service_type": r["service_type"],
                "avg_min": avg,
                "samples": n,
            }
        )

    daily = []
    for d in sorted(by_date.keys()):
        n = by_date_samples[d]
        avg = round(by_date_weighted.get(d, 0.0) / n, 2) if n else None
        daily.append({"date": d, "avg_min": avg, "samples": n})

    days = []
    for r in daily:
        offenders = sorted(
            by_date.get(r["date"], []),
            key=lambda x: (x["avg_min"] is None, -(x["avg_min"] or 0)),
        )[:top_offenders]
        days.append(
            {
                "date": r["date"].isoformat(),
                "avg_min": r["avg_min"],
                "samples": r["samples"],
                "top_offenders": offenders,
            }
        )
    return {"days": days}
