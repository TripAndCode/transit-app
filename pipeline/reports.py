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

from datetime import date, timedelta

from api.range import RangeCtx, build_agg_daily_trend_filter, build_updates_filter
from pipeline.cache import async_lru_cache
from pipeline.db import build_dedup_inner_sql


def _dedup_cte(where_frag: str) -> str:
    """Wrap the shared latest-by-captured_at dedup SQL in a `deduped` CTE.

    `where_frag` is a trusted server-built fragment from
    `api.range.build_updates_filter` — never user input.
    """
    return f"deduped AS ({build_dedup_inner_sql(placeholder='$1', extra_where=where_frag)})"


def _agg_filter(ctx: RangeCtx, next_param: int) -> tuple[str, list, int]:
    """WHERE fragment for ``agg_daily_trend`` covering date + DOW + service + routes.

    Wraps :func:`api.range.build_agg_daily_trend_filter` (which only emits date
    + DOW) and tacks on optional ``service_type`` + ``route_code`` predicates
    so every Overview helper that reads ``agg_daily_trend`` shares the same
    filter shape.

    The ``time_band`` filter is silently dropped — the agg tables roll up to
    (date, route, service) granularity and have no hour-of-day column. Callers
    filtering by ``time_band`` will see the unfiltered headline. See the
    docstring of each helper for the caveat.
    """
    frag, params, n = build_agg_daily_trend_filter(ctx, next_param)
    parts: list[str] = [frag] if frag else []
    if ctx.service != "all":
        parts.append(f"service_type = ${n}")
        params.append(ctx.service)
        n += 1
    if ctx.routes:
        parts.append(f"route_code = ANY(${n}::text[])")
        params.append(list(ctx.routes))
        n += 1
    return " AND ".join(parts), params, n


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


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


@async_lru_cache(maxsize=64, ttl_seconds=300)
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


@async_lru_cache(maxsize=32, ttl_seconds=300)
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
    # Single SQL: build dedup + per_day in one CTE chain, then derive the
    # daily aggregate from per_day so we touch ``updates`` exactly once.
    sql = (
        f"WITH {_dedup_cte(where)},\n"
        "per_day AS (\n"
        "    SELECT date, route_code, service_type,\n"
        "           ROUND(AVG(dep_delay)/60.0::numeric, 2) AS avg_min,\n"
        "           COUNT(*) AS samples\n"
        "    FROM deduped GROUP BY date, route_code, service_type\n"
        "    HAVING COUNT(*) > 5\n"
        ")\n"
        "SELECT * FROM per_day"
    )
    per_day = await conn.fetch(sql, agency_id, *params)

    by_date_samples: dict = {}
    by_date_weighted: dict = {}
    by_date: dict = {}
    for r in per_day:
        d = r["date"]
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


# ---------------------------------------------------------------------------
# Overview tab
# ---------------------------------------------------------------------------


async def _latest_data_date(agency_id: int, ctx: RangeCtx, conn) -> date | None:
    """Most recent date inside ctx that has any aggregated samples.

    Used to anchor the headline's 7-day window to where data actually
    exists. Keeps the "this week vs last week" semantics meaningful
    when ingest is lagging or the user selects a wide historical range.

    Reads from ``agg_daily_trend`` for sub-millisecond response. The
    ``time_band`` filter is dropped here — see :func:`_agg_filter`.
    """
    where, params, _ = _agg_filter(ctx, next_param=2)
    where_clause = f" AND ({where})" if where else ""
    sql = f"SELECT MAX(date::date) AS d FROM agg_daily_trend WHERE agency_id=$1{where_clause}"
    row = await conn.fetchrow(sql, agency_id, *params)
    return row["d"] if row and row["d"] else None


def _baseline_ctx(ctx: RangeCtx) -> RangeCtx:
    """Build the comparison-baseline ctx for the headline + movers.

    Compares the most recent 7 days of ``ctx`` against the 7-day window
    one week earlier. This keeps the delta semantically a true week-over-
    week even when the user has widened the filter to a longer range.
    """
    end_current = ctx.to_date
    start_current = end_current - timedelta(days=6)  # most-recent 7d
    baseline_end = start_current - timedelta(days=1)
    baseline_start = baseline_end - timedelta(days=6)
    return RangeCtx(
        from_date=baseline_start,
        to_date=baseline_end,
        dow=ctx.dow,
        time_band=ctx.time_band,
        service=ctx.service,
        routes=ctx.routes,
    )


async def _headline_stats(agency_id: int, ctx: RangeCtx, conn) -> tuple[float | None, int]:
    """Return (avg_min, samples) for the headline over ``ctx``.

    Reads from ``agg_daily_trend``. The aggregate is a sample-weighted
    average so days with more observations weigh proportionally — this
    matches the row-level mean the dedup CTE used to compute.

    The ``time_band`` filter is silently dropped — ``agg_daily_trend``
    has no hour-of-day dimension. Callers using ``time_band`` see the
    unfiltered headline. ``service`` and ``routes`` filters still apply.
    """
    where, params, _ = _agg_filter(ctx, next_param=2)
    where_clause = f" AND ({where})" if where else ""
    sql = (
        "SELECT CASE WHEN SUM(samples) > 0\n"
        "            THEN ROUND((SUM(avg_min * samples) / NULLIF(SUM(samples), 0))::numeric, 2)\n"
        "            ELSE NULL END AS avg_min,\n"
        "       COALESCE(SUM(samples), 0)::int AS samples\n"
        "FROM agg_daily_trend\n"
        f"WHERE agency_id=$1{where_clause}"
    )
    row = await conn.fetchrow(sql, agency_id, *params)
    avg = float(row["avg_min"]) if row["avg_min"] is not None else None
    return avg, int(row["samples"] or 0)


async def _per_route_avg(agency_id: int, ctx: RangeCtx, conn) -> dict[str, tuple[float, int]]:
    """Per-route avg_min + samples for ``ctx``. Keyed by route_code.

    Reads from ``agg_daily_trend`` and computes a sample-weighted average.
    The ``time_band`` filter is silently dropped — see :func:`_agg_filter`.
    """
    where, params, _ = _agg_filter(ctx, next_param=2)
    where_clause = f" AND ({where})" if where else ""
    sql = (
        "SELECT route_code,\n"
        "       (SUM(avg_min * samples) / NULLIF(SUM(samples), 0))::numeric AS avg_min,\n"
        "       SUM(samples)::int AS samples\n"
        "FROM agg_daily_trend\n"
        f"WHERE agency_id=$1{where_clause}\n"
        "GROUP BY route_code\n"
        "HAVING SUM(samples) > 0 AND SUM(avg_min * samples) IS NOT NULL"
    )
    rows = await conn.fetch(sql, agency_id, *params)
    return {r["route_code"]: (float(r["avg_min"]), int(r["samples"])) for r in rows}


async def _route_short_names(agency_id: int, route_codes: list[str], conn) -> dict[str, str | None]:
    """Resolve route_short_name for a list of route_codes.

    `route_code` is the digit suffix inside `route_id`'s trailing `(NNNN)`
    (same regex used by api/routers/static.py:list_routes end-to-end).
    Routes without a matching static_routes row map to None.
    """
    if not route_codes:
        return {}
    rows = await conn.fetch(
        "SELECT regexp_replace(route_id, '.*\\((\\d+)\\)$', '\\1') AS route_code, "
        "       route_short_name "
        "FROM static_routes "
        "WHERE agency_id=$1 "
        "  AND regexp_replace(route_id, '.*\\((\\d+)\\)$', '\\1') = ANY($2::text[])",
        agency_id,
        list(route_codes),
    )
    return {r["route_code"]: r["route_short_name"] for r in rows}


async def _route_weekly_history(
    agency_id: int,
    route_codes: list[str],
    ctx: RangeCtx,
    conn,
    weeks_back: int = 4,
) -> dict[str, list[float | None]]:
    """Per-route weekly avg_min for the last ``weeks_back`` true 7-day
    buckets ending at ``ctx.to_date``. Honors DOW / service / routes via
    :func:`_agg_filter` and reads from ``agg_daily_trend``.

    The ``time_band`` filter is silently dropped — see :func:`_agg_filter`.
    """
    if not route_codes:
        return {}

    out: dict[str, list[float | None]] = {code: [] for code in route_codes}
    for k in range(weeks_back - 1, -1, -1):
        end = ctx.to_date - timedelta(days=7 * k)
        start = end - timedelta(days=6)  # inclusive 7-day window
        window_ctx = RangeCtx(
            from_date=start,
            to_date=end,
            dow=ctx.dow,
            time_band=ctx.time_band,
            service=ctx.service,
            routes=ctx.routes,
        )
        where, params, n = _agg_filter(window_ctx, next_param=2)
        where_clause = f" AND ({where})" if where else ""
        rows = await conn.fetch(
            "SELECT route_code,\n"
            "       (SUM(avg_min * samples) / NULLIF(SUM(samples), 0))::numeric AS avg_min\n"
            "FROM agg_daily_trend\n"
            f"WHERE agency_id=$1{where_clause}\n"
            f"  AND route_code = ANY(${n}::text[])\n"
            "GROUP BY route_code",
            agency_id,
            *params,
            list(route_codes),
        )
        wk_map = {r["route_code"]: float(r["avg_min"]) for r in rows if r["avg_min"] is not None}
        for code in route_codes:
            out[code].append(wk_map.get(code))
    return out


def _streak_weeks(history: list[float | None], *, direction: str) -> int:
    """Count trailing consecutive weeks where each week is worse (up) or
    better (down) than the prior week. Stops at the first non-matching or
    None pair. Caller passes oldest-first history; we scan from end backwards."""
    if len(history) < 2:
        return 0
    count = 0
    for i in range(len(history) - 1, 0, -1):
        cur = history[i]
        prev = history[i - 1]
        if cur is None or prev is None:
            break
        if direction == "up" and cur > prev:
            count += 1
        elif direction == "down" and cur < prev:
            count += 1
        else:
            break
    return count


async def _concentration(agency_id: int, ctx: RangeCtx, conn) -> dict:
    """Top-3 routes by total positive delay contribution + rest share.

    Reads from ``agg_daily_trend`` and approximates the per-row
    ``SUM(GREATEST(dep_delay, 0))`` of the old implementation as
    ``SUM(GREATEST(avg_min, 0) * samples)`` per route, working in minutes
    instead of seconds. Routes that ran early on net (negative ``avg_min``
    for a given date) contribute zero — same intent as the original
    metric: contribution to LATENESS, not to the signed sum.

    The ``time_band`` filter is silently dropped — see :func:`_agg_filter`.
    """
    where, params, _ = _agg_filter(ctx, next_param=2)
    where_clause = f" AND ({where})" if where else ""
    rows = await conn.fetch(
        "SELECT route_code,\n"
        "       SUM(GREATEST(avg_min, 0) * samples)::float AS total_late_min\n"
        "FROM agg_daily_trend\n"
        f"WHERE agency_id=$1{where_clause}\n"
        "GROUP BY route_code\n"
        "ORDER BY total_late_min DESC NULLS LAST",
        agency_id,
        *params,
    )
    if not rows:
        return {"top_routes": [], "rest_share_pct": 0.0}
    grand_total = sum(float(r["total_late_min"] or 0.0) for r in rows)
    if grand_total == 0:
        return {"top_routes": [], "rest_share_pct": 0.0}
    top_3 = rows[:3]
    codes = [r["route_code"] for r in top_3]
    names = await _route_short_names(agency_id, codes, conn)
    top_3_sum = sum(float(r["total_late_min"] or 0.0) for r in top_3)
    return {
        "top_routes": [
            {
                "route_code": r["route_code"],
                "route_short_name": names.get(r["route_code"]),
                "share_pct": round((float(r["total_late_min"] or 0.0) / grand_total) * 100.0, 1),
            }
            for r in top_3
        ],
        "rest_share_pct": round(((grand_total - top_3_sum) / grand_total) * 100.0, 1),
    }


async def _peak_hour(agency_id: int, ctx: RangeCtx, conn) -> dict | None:
    """24-bucket avg by EXTRACT(HOUR FROM scheduled_time) + peak hour.

    Reads from ``agg_route_hour``, which is a fixed analyze-period rollup
    (no date column). Consequence: the date range, DOW, and ``time_band``
    in ``ctx`` are all ignored — only ``service`` and ``routes`` apply.
    The frontend can flag this caveat if needed.
    """
    n = 2
    params: list = []
    parts: list[str] = []
    if ctx.service != "all":
        parts.append(f"service_type = ${n}")
        params.append(ctx.service)
        n += 1
    if ctx.routes:
        parts.append(f"route_code = ANY(${n}::text[])")
        params.append(list(ctx.routes))
        n += 1
    where_clause = (" AND " + " AND ".join(parts)) if parts else ""
    sql = (
        "SELECT EXTRACT(HOUR FROM scheduled_time)::int AS h,\n"
        "       (SUM(avg_min * samples) / NULLIF(SUM(samples), 0))::numeric AS avg_min\n"
        "FROM agg_route_hour\n"
        f"WHERE agency_id=$1{where_clause}\n"
        "GROUP BY EXTRACT(HOUR FROM scheduled_time)"
    )
    rows = await conn.fetch(sql, agency_id, *params)
    if not rows:
        return None
    by_hour: list[float | None] = [None] * 24
    for r in rows:
        if r["avg_min"] is None:
            continue
        h = int(r["h"])
        if 0 <= h < 24:
            by_hour[h] = round(float(r["avg_min"]), 2)
    valid_idx = [h for h in range(24) if by_hour[h] is not None]
    if not valid_idx:
        return None
    peak_h = max(valid_idx, key=lambda h: by_hour[h])
    return {
        "by_hour": by_hour,
        "peak_hour": peak_h,
        "peak_avg_min": by_hour[peak_h],
    }


async def _movers(agency_id: int, cur_ctx: RangeCtx, base_ctx: RangeCtx, conn) -> dict:
    """Top-3 worsened + top-3 improved routes by signed delta_min.

    Compares ``cur_ctx`` against ``base_ctx`` (both built upstream by
    ``compute_overview_summary`` so the comparison is a true 7-day
    week-over-week regardless of the user's selected range). Requires
    >= 10 samples in BOTH windows for a route to enter the ranking — a
    route with a handful of obs can swing a huge delta_pct and would
    otherwise dominate top-3 with low statistical confidence.
    """
    cur = await _per_route_avg(agency_id, cur_ctx, conn)
    prv = await _per_route_avg(agency_id, base_ctx, conn)
    common = set(cur) & set(prv)
    deltas: list[tuple[str, float, float]] = []
    MIN_SAMPLES = 10
    for code in common:
        cur_avg, cur_n = cur[code]
        prv_avg, prv_n = prv[code]
        if prv_avg == 0:
            continue
        if cur_n < MIN_SAMPLES or prv_n < MIN_SAMPLES:
            continue
        d_min = cur_avg - prv_avg
        d_pct = (d_min / prv_avg) * 100.0
        deltas.append((code, round(d_min, 2), round(d_pct, 1)))
    deltas.sort(key=lambda x: x[1])
    better = deltas[:3]  # smallest (most negative) first, [0] is best
    worse = list(reversed(deltas[-3:]))  # largest positive first
    # Avoid duplicates when there are fewer than 6 routes
    worse_codes = {c for c, _, _ in worse}
    better = [item for item in better if item[0] not in worse_codes]
    codes = [c for c, _, _ in worse + better]
    names = await _route_short_names(agency_id, codes, conn)
    history = await _route_weekly_history(agency_id, codes, cur_ctx, conn, weeks_back=4)

    def _entry(code, dm, dp, direction):
        pts = [v for v in history.get(code, []) if v is not None]
        return {
            "route_code": code,
            "route_short_name": names.get(code),
            "delta_min": dm,
            "delta_pct": dp,
            "streak_weeks": _streak_weeks(history.get(code, []), direction=direction),
            "sparkline_points": pts,
        }

    return {
        "worse": [_entry(c, dm, dp, "up") for c, dm, dp in worse],
        "better": [_entry(c, dm, dp, "down") for c, dm, dp in better],
    }


async def _service_split(agency_id: int, ctx: RangeCtx, conn) -> dict[str, float]:
    """avg_min per service_type (typically '平日' / '土日祝').

    Reads from ``agg_daily_trend`` with a sample-weighted average. The
    ``time_band`` filter is silently dropped — see :func:`_agg_filter`.
    """
    where, params, _ = _agg_filter(ctx, next_param=2)
    where_clause = f" AND ({where})" if where else ""
    rows = await conn.fetch(
        "SELECT service_type,\n"
        "       (SUM(avg_min * samples) / NULLIF(SUM(samples), 0))::numeric AS avg_min\n"
        "FROM agg_daily_trend\n"
        f"WHERE agency_id=$1{where_clause}\n"
        "GROUP BY service_type",
        agency_id,
        *params,
    )
    return {
        r["service_type"]: round(float(r["avg_min"]), 2) for r in rows if r["service_type"] and r["avg_min"] is not None
    }


async def _daily_sparkline(agency_id: int, ctx: RangeCtx, conn) -> list[float]:
    """Up to 7 daily avg_min points (oldest first) inside ``ctx``.

    Reads from ``agg_daily_trend`` with a sample-weighted average per
    date. The ``time_band`` filter is silently dropped — see
    :func:`_agg_filter`.
    """
    where, params, _ = _agg_filter(ctx, next_param=2)
    where_clause = f" AND ({where})" if where else ""
    rows = await conn.fetch(
        "SELECT date AS day,\n"
        "       (SUM(avg_min * samples) / NULLIF(SUM(samples), 0))::numeric AS avg_min\n"
        "FROM agg_daily_trend\n"
        f"WHERE agency_id=$1{where_clause}\n"
        "GROUP BY date\n"
        "ORDER BY date ASC",
        agency_id,
        *params,
    )
    pts = [round(float(r["avg_min"]), 2) for r in rows if r["avg_min"] is not None]
    return pts[-7:]


@async_lru_cache(maxsize=64, ttl_seconds=300)
async def compute_overview_summary(
    agency_id: int,
    ctx: RangeCtx,
    conn,
    locale: str = "ja",
) -> dict:
    """Build the 概況 payload for one agency over ``ctx``.

    Headline math uses the LAST 7 days of ``ctx`` and compares against
    the 7-day window immediately prior, so the "this week vs last week"
    copy is honest regardless of how the user has widened the ctx range.
    Concentration / peak / service_split / sparkline still aggregate over
    the full ctx to surface broader patterns.
    """
    latest = await _latest_data_date(agency_id, ctx, conn)
    # If no data anywhere in ctx, anchor to ctx.to_date so empty payload
    # still has a sensible window_to.
    anchor = latest if latest is not None else ctx.to_date

    # Build current + baseline 7-day windows anchored at `anchor`, but
    # clamped inside ctx.
    cur_to = anchor
    cur_from = max(cur_to - timedelta(days=6), ctx.from_date)
    cur_ctx = RangeCtx(
        from_date=cur_from,
        to_date=cur_to,
        dow=ctx.dow,
        time_band=ctx.time_band,
        service=ctx.service,
        routes=ctx.routes,
    )
    base_to = cur_from - timedelta(days=1)
    base_from = base_to - timedelta(days=6)
    base_ctx = RangeCtx(
        from_date=base_from,
        to_date=base_to,
        dow=ctx.dow,
        time_band=ctx.time_band,
        service=ctx.service,
        routes=ctx.routes,
    )

    avg_min, samples = await _headline_stats(agency_id, cur_ctx, conn)
    baseline_avg, _ = await _headline_stats(agency_id, base_ctx, conn)

    delta_min = None
    delta_pct = None
    if avg_min is not None and baseline_avg is not None:
        delta_min = round(avg_min - baseline_avg, 2)
        if baseline_avg != 0:
            delta_pct = round((delta_min / baseline_avg) * 100.0, 1)

    movers = await _movers(agency_id, cur_ctx, base_ctx, conn)
    concentration = await _concentration(agency_id, ctx, conn)
    peak = await _peak_hour(agency_id, ctx, conn)
    service_split = await _service_split(agency_id, ctx, conn)
    sparkline_points = await _daily_sparkline(agency_id, cur_ctx, conn)

    return {
        "headline": {
            "avg_min": avg_min,
            "baseline_avg_min": baseline_avg,
            "delta_min": delta_min,
            "delta_pct": delta_pct,
            "samples": samples,
            "window_from": cur_ctx.from_date.isoformat(),
            "window_to": cur_ctx.to_date.isoformat(),
        },
        "movers": movers,
        "concentration": concentration,
        "peak_hour": peak,
        "service_split": service_split,
        "sparkline_points": sparkline_points,
    }
