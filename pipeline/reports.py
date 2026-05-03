"""Live report computations honoring :class:`~api.range.RangeCtx`.

v1 served reports from a pre-rendered ``snapshots`` table. v2 computes them
on demand from the ``updates`` table so the user's time-range / DOW /
time-band filter actually changes the numbers.

All compute functions take ``(agency_id, ctx, conn, **kw)`` and return a
list of tuples in the shape the existing formatters in
``pipeline.query.formatter`` already know how to render.

The dedup CTE is the same logic the v1 batch ``analyze`` used (one row per
``(route_code, service_type, scheduled_time, trip_id, date, stop_sequence)``),
ported to asyncpg-style ``$N`` placeholders + an injected WHERE fragment.
"""

from __future__ import annotations

from api.range import RangeCtx, build_updates_filter


def _dedup_cte(where_frag: str) -> str:
    """The v1 dedup CTE, parameterized with the ctx WHERE fragment."""
    return (
        "deduped AS (\n"
        "    SELECT route_code, service_type, scheduled_time, trip_id,\n"
        "           captured_at::date AS date, stop_sequence,\n"
        "           MAX(dep_delay) AS dep_delay\n"
        "    FROM updates\n"
        f"    WHERE agency_id = $1 AND dep_delay IS NOT NULL AND {where_frag}\n"
        "    GROUP BY route_code, service_type, scheduled_time, trip_id,\n"
        "             captured_at::date, stop_sequence\n"
        ")"
    )


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


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


async def compute_on_time(
    agency_id: int,
    ctx: RangeCtx,
    conn,
    threshold_sec: int = 60,
    limit: int = 100,
) -> list[tuple]:
    """On-time percentage per route-service. ``threshold_sec`` is the cutoff."""
    where, params, n = build_updates_filter(ctx, next_param=2)
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
        "ORDER BY on_time_pct DESC NULLS LAST\n"
        f"LIMIT ${n}"
    )
    rows = await conn.fetch(sql, agency_id, *params, limit)
    return [tuple(r) for r in rows]


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


async def compute_dow_ranking(
    agency_id: int,
    ctx: RangeCtx,
    conn,
    dow_group: str,  # 'weekday' or 'weekend'
    limit: int = 100,
) -> list[tuple]:
    """Worst-delay routes restricted to weekday or weekend observations."""
    # Override ctx.dow with the report-specific group; user's global dow is
    # ignored here on purpose — the report's identity IS its DOW filter.
    overridden = RangeCtx(
        from_date=ctx.from_date,
        to_date=ctx.to_date,
        dow=dow_group,  # type: ignore[arg-type]
        time_band=ctx.time_band,
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


async def compute_compare_ranking(
    agency_id: int,
    ctx: RangeCtx,
    conn,
    limit: int = 100,
) -> list[tuple]:
    """Per-route weekday-vs-weekend delay difference, sorted by absolute delta."""
    weekday_ctx = RangeCtx(
        from_date=ctx.from_date,
        to_date=ctx.to_date,
        dow="weekday",
        time_band=ctx.time_band,
    )
    weekend_ctx = RangeCtx(
        from_date=ctx.from_date,
        to_date=ctx.to_date,
        dow="weekend",
        time_band=ctx.time_band,
    )
    wd_where, wd_params, n_after_wd = build_updates_filter(weekday_ctx, next_param=2)
    we_where, we_params, n = build_updates_filter(weekend_ctx, next_param=n_after_wd)
    sql = (
        "WITH wd_dedup AS (\n"
        "    SELECT route_code, trip_id,\n"
        "           captured_at::date AS date, stop_sequence,\n"
        "           MAX(dep_delay) AS dep_delay\n"
        "    FROM updates\n"
        f"    WHERE agency_id = $1 AND dep_delay IS NOT NULL AND {wd_where}\n"
        "    GROUP BY route_code, trip_id, captured_at::date, stop_sequence\n"
        "),\n"
        "we_dedup AS (\n"
        "    SELECT route_code, trip_id,\n"
        "           captured_at::date AS date, stop_sequence,\n"
        "           MAX(dep_delay) AS dep_delay\n"
        "    FROM updates\n"
        f"    WHERE agency_id = $1 AND dep_delay IS NOT NULL AND {we_where}\n"
        "    GROUP BY route_code, trip_id, captured_at::date, stop_sequence\n"
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


async def compute_trend_series(
    agency_id: int,
    ctx: RangeCtx,
    conn,
    top_offenders: int = 3,
) -> dict:
    """Daily series + per-day worst-route attribution for the Trend chart.

    Returns ``{ days: [{ date, avg_min, samples, top_offenders: [...] }] }``.
    """
    where, params, _ = build_updates_filter(ctx, next_param=2)
    daily_sql = (
        f"WITH {_dedup_cte(where)}\n"
        "SELECT date, ROUND(AVG(dep_delay)/60.0::numeric, 2) AS avg_min, COUNT(*) AS samples\n"
        "FROM deduped GROUP BY date ORDER BY date"
    )
    daily = await conn.fetch(daily_sql, agency_id, *params)

    per_day_sql = (
        f"WITH {_dedup_cte(where)}\n"
        "SELECT date, route_code, service_type,\n"
        "       ROUND(AVG(dep_delay)/60.0::numeric, 2) AS avg_min,\n"
        "       COUNT(*) AS samples\n"
        "FROM deduped GROUP BY date, route_code, service_type\n"
        "HAVING COUNT(*) > 5"
    )
    per_day = await conn.fetch(per_day_sql, agency_id, *params)

    by_date: dict = {}
    for r in per_day:
        by_date.setdefault(r["date"], []).append(
            {
                "route_code": r["route_code"],
                "service_type": r["service_type"],
                "avg_min": float(r["avg_min"]) if r["avg_min"] is not None else None,
                "samples": r["samples"],
            }
        )

    days = []
    for r in daily:
        offenders = sorted(
            by_date.get(r["date"], []),
            key=lambda x: (x["avg_min"] is None, -(x["avg_min"] or 0)),
        )[:top_offenders]
        days.append(
            {
                "date": r["date"].isoformat(),
                "avg_min": float(r["avg_min"]) if r["avg_min"] is not None else None,
                "samples": r["samples"],
                "top_offenders": offenders,
            }
        )
    return {"days": days}
