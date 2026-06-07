"""The seven report-tab compute_* functions (ranking, on-time, trend, etc.)."""

from __future__ import annotations

from api.range import RangeCtx, build_updates_filter
from pipeline import perf
from pipeline.cache import async_lru_cache
from pipeline.reports.filters import _dedup_cte


@perf.timed("reports.ranking")
@async_lru_cache(maxsize=64, ttl_seconds=300)
async def compute_ranking(
    agency_id: int,
    ctx: RangeCtx,
    conn,
    sort_order: str = "desc",
    limit: int = 100,
) -> list[tuple]:
    """Routes ranked by average delay over ctx. ``sort_order='asc'`` → best first."""
    where, params, n = build_updates_filter(ctx, next_param=2)
    order = "DESC" if sort_order.lower() == "desc" else "ASC"
    sql = (
        f"WITH {_dedup_cte(where)},\n"
        "ranked AS (\n"
        "    SELECT *, PERCENT_RANK() OVER (\n"
        "        PARTITION BY route_code, service_type ORDER BY dep_delay\n"
        "    ) AS pct FROM deduped\n"
        ")\n"
        "SELECT route_code, service_type,\n"
        "       ROUND(AVG(dep_delay)/60.0::numeric, 2) AS avg_min,\n"
        "       ROUND(MIN(CASE WHEN pct >= 0.5 THEN dep_delay END)/60.0::numeric, 2) AS p50_min,\n"
        "       ROUND(MIN(CASE WHEN pct >= 0.9 THEN dep_delay END)/60.0::numeric, 2) AS p90_min,\n"
        "       COUNT(*) AS samples\n"
        "FROM ranked\n"
        "GROUP BY route_code, service_type\n"
        "HAVING COUNT(*) > 20\n"
        f"ORDER BY avg_min {order} NULLS LAST\n"
        f"LIMIT ${n}"
    )
    rows = await conn.fetch(sql, agency_id, *params, limit)
    return [tuple(r) for r in rows]


@perf.timed("reports.on_time")
@async_lru_cache(maxsize=64, ttl_seconds=300)
async def compute_on_time(
    agency_id: int,
    ctx: RangeCtx,
    conn,
    threshold_sec: int = 60,
    limit: int = 100,
    sort_order: str = "desc",
) -> list[tuple]:
    """On-time percentage per route-service. ``threshold_sec`` is the cutoff.

    ``sort_order='desc'`` returns best on-time routes first (highest %);
    ``sort_order='asc'`` returns worst routes first (lowest %) for BUG-3.
    """
    where, params, n = build_updates_filter(ctx, next_param=2)
    order = "DESC" if sort_order.lower() == "desc" else "ASC"
    sql = (
        f"WITH {_dedup_cte(where)}\n"
        "SELECT route_code, service_type,\n"
        "       ROUND(SUM(CASE WHEN dep_delay <= "
        f"{threshold_sec}"
        " THEN 1.0 ELSE 0 END)*100.0/COUNT(*), 1) AS on_time_pct,\n"
        "       ROUND(AVG(dep_delay)/60.0::numeric, 2) AS avg_min,\n"
        "       COUNT(*) AS samples\n"
        "FROM deduped\n"
        "GROUP BY route_code, service_type\n"
        "HAVING COUNT(*) > 20\n"
        f"ORDER BY on_time_pct {order} NULLS LAST\n"
        f"LIMIT ${n}"
    )
    rows = await conn.fetch(sql, agency_id, *params, limit)
    return [tuple(r) for r in rows]


@perf.timed("reports.worst_5min")
@async_lru_cache(maxsize=64, ttl_seconds=300)
async def compute_worst_5min(
    agency_id: int,
    ctx: RangeCtx,
    conn,
    limit: int = 100,
) -> list[tuple]:
    """Routes ranked by count of >5min late observations."""
    where, params, n = build_updates_filter(ctx, next_param=2)
    sql = (
        f"WITH {_dedup_cte(where)}\n"
        "SELECT route_code, service_type,\n"
        "       SUM(CASE WHEN dep_delay > 300 THEN 1 ELSE 0 END) AS late5_count,\n"
        "       ROUND(AVG(dep_delay)/60.0::numeric, 2) AS avg_min,\n"
        "       COUNT(*) AS samples\n"
        "FROM deduped\n"
        "GROUP BY route_code, service_type\n"
        "HAVING SUM(CASE WHEN dep_delay > 300 THEN 1 ELSE 0 END) > 0\n"
        "ORDER BY late5_count DESC\n"
        f"LIMIT ${n}"
    )
    rows = await conn.fetch(sql, agency_id, *params, limit)
    return [tuple(r) for r in rows]


@perf.timed("reports.dow_ranking")
@async_lru_cache(maxsize=64, ttl_seconds=300)
async def compute_dow_ranking(
    agency_id: int,
    ctx: RangeCtx,
    conn,
    dow_group: str,  # 'weekday' or 'weekend'
    limit: int = 100,
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
    where, params, n = build_updates_filter(overridden, next_param=2)
    label = "週末" if dow_group == "weekend" else "平日"
    sql = (
        f"WITH {_dedup_cte(where)}\n"
        f"SELECT route_code, service_type, '{label}' AS dow,\n"
        "       ROUND(AVG(dep_delay)/60.0::numeric, 2) AS avg_min,\n"
        "       COUNT(*) AS samples\n"
        "FROM deduped\n"
        "GROUP BY route_code, service_type\n"
        "HAVING COUNT(*) > 10\n"
        "ORDER BY avg_min DESC NULLS LAST\n"
        f"LIMIT ${n}"
    )
    rows = await conn.fetch(sql, agency_id, *params, limit)
    return [tuple(r) for r in rows]


@perf.timed("reports.compare_ranking")
@async_lru_cache(maxsize=64, ttl_seconds=300)
async def compute_compare_ranking(
    agency_id: int,
    ctx: RangeCtx,
    conn,
    limit: int = 100,
) -> list[tuple]:
    """Per-route weekday-vs-weekend delay difference, sorted by absolute delta.

    Drops the user's ``service`` filter (same reason as compute_dow_ranking)
    but preserves ``routes`` so route-restricted comparisons work.
    """
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
    wd_where, wd_params, n_after_wd = build_updates_filter(weekday_ctx, next_param=2)
    we_where, we_params, n = build_updates_filter(weekend_ctx, next_param=n_after_wd)
    sql = (
        # Narrower dedup key than _dedup_cte (no service_type/scheduled_time):
        # the weekday-vs-weekend rollup doesn't need them. Assumes
        # (trip_id, date) determines service_type in clean data.
        "WITH wd_dedup AS (\n"
        "    SELECT DISTINCT ON (route_code, trip_id,\n"
        "                        captured_at::date, stop_sequence)\n"
        "           route_code, trip_id,\n"
        "           captured_at::date AS date, stop_sequence, dep_delay\n"
        "    FROM updates\n"
        f"    WHERE agency_id = $1 AND dep_delay IS NOT NULL AND {wd_where}\n"
        "    ORDER BY route_code, trip_id, captured_at::date,\n"
        "             stop_sequence, captured_at DESC, id DESC\n"
        "),\n"
        "we_dedup AS (\n"
        "    SELECT DISTINCT ON (route_code, trip_id,\n"
        "                        captured_at::date, stop_sequence)\n"
        "           route_code, trip_id,\n"
        "           captured_at::date AS date, stop_sequence, dep_delay\n"
        "    FROM updates\n"
        f"    WHERE agency_id = $1 AND dep_delay IS NOT NULL AND {we_where}\n"
        "    ORDER BY route_code, trip_id, captured_at::date,\n"
        "             stop_sequence, captured_at DESC, id DESC\n"
        "),\n"
        "wd_avg AS (\n"
        "    SELECT route_code, AVG(dep_delay)/60.0 AS avg_min, COUNT(*) AS n\n"
        "    FROM wd_dedup GROUP BY route_code HAVING COUNT(*) > 10\n"
        "),\n"
        "we_avg AS (\n"
        "    SELECT route_code, AVG(dep_delay)/60.0 AS avg_min, COUNT(*) AS n\n"
        "    FROM we_dedup GROUP BY route_code HAVING COUNT(*) > 10\n"
        ")\n"
        "SELECT wd.route_code,\n"
        "       ROUND(wd.avg_min::numeric, 2) AS heijitsu,\n"
        "       ROUND(we.avg_min::numeric, 2) AS kyujitsu,\n"
        "       ROUND(ABS(wd.avg_min - we.avg_min)::numeric, 2) AS abs_delta,\n"
        "       ROUND((we.avg_min - wd.avg_min)::numeric, 2) AS signed_delta\n"
        "FROM wd_avg wd JOIN we_avg we ON wd.route_code = we.route_code\n"
        "ORDER BY ABS(wd.avg_min - we.avg_min) DESC\n"
        f"LIMIT ${n}"
    )
    rows = await conn.fetch(sql, agency_id, *wd_params, *we_params, limit)
    return [tuple(r) for r in rows]


# ---------------------------------------------------------------------------
# Trend (new endpoint, not just a report row)
# ---------------------------------------------------------------------------


@perf.timed("reports.hourly_heatmap")
@async_lru_cache(maxsize=16, ttl_seconds=300)
async def compute_hourly_heatmap(
    agency_id: int,
    ctx: RangeCtx,
    conn,
) -> list[dict]:
    """Hour-of-day × date cells for the granular trend view.

    Returns ``[ { date, hour, avg_min, samples } ]`` filtered by the same
    ctx the rest of the trend uses. Hour is extracted from
    ``scheduled_time`` (a TIME column post migration 0011); cells with too
    few samples (<3) are dropped to keep the rendering signal-strong.
    """
    where, params, _ = build_updates_filter(ctx, next_param=2)
    sql = (
        f"WITH {_dedup_cte(where)}\n"
        "SELECT date, EXTRACT(HOUR FROM scheduled_time)::int AS hour,\n"
        "       ROUND(AVG(dep_delay)/60.0::numeric, 2) AS avg_min,\n"
        "       COUNT(*) AS samples\n"
        "FROM deduped\n"
        "WHERE scheduled_time IS NOT NULL\n"
        "GROUP BY date, EXTRACT(HOUR FROM scheduled_time)::int\n"
        "HAVING COUNT(*) >= 3\n"
        "ORDER BY date, hour"
    )
    rows = await conn.fetch(sql, agency_id, *params)
    return [
        {
            "date": r["date"].isoformat(),
            "hour": r["hour"],
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
) -> dict:
    """Bucketed series + per-bucket worst-route attribution for the Trend chart.

    ``granularity`` controls the time bucket: ``'day'`` (default), ``'week'``,
    or ``'month'``. Returns
    ``{ days: [{ date, avg_min, samples, top_offenders: [...] }] }``
    where ``date`` is the bucket start date (ISO string).
    """
    where, params, _ = build_updates_filter(ctx, next_param=2)

    # Map granularity to a date_trunc unit; fall back to 'day' for unknown values.
    _TRUNC = {"day": "day", "week": "week", "month": "month"}
    trunc_unit = _TRUNC.get(granularity, "day")

    # Single SQL: build dedup + per_bucket in one CTE chain.
    # date_trunc on a DATE requires casting to timestamp and back to date.
    sql = (
        f"WITH {_dedup_cte(where)},\n"
        "per_bucket AS (\n"
        f"    SELECT date_trunc('{trunc_unit}', date::timestamp)::date AS bucket,\n"
        "           route_code, service_type,\n"
        "           ROUND(AVG(dep_delay)/60.0::numeric, 2) AS avg_min,\n"
        "           COUNT(*) AS samples\n"
        "    FROM deduped GROUP BY bucket, route_code, service_type\n"
        "    HAVING COUNT(*) > 5\n"
        ")\n"
        "SELECT * FROM per_bucket"
    )
    per_day = await conn.fetch(sql, agency_id, *params)

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
