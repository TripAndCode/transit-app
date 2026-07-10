"""The 概況 magazine payload and its private stage-query helpers.

Public surface
--------------
compute_overview_summary(agency_id, ctx, conn, locale, *, pool=None) -> dict
    Build the full Overview tab payload.  Pass ``pool`` to run the ten
    stage queries concurrently (each task acquires its own connection);
    omit it (or pass ``None``) for the sequential single-connection path
    used by tests and ad-hoc callers.

Fast vs slow paths
------------------
Most helpers have two internal branches:

* **Fast path** (``ctx.time_band == "all"``) — reads the pre-aggregated
  ``agg_daily_trend`` / ``agg_route_hour`` tables, sub-millisecond even
  over multi-month ranges.
* **Slow path** (any other time_band) — falls back to the live ``updates``
  table via the dedup CTE so the hour-of-day filter is honoured.

``_peak_hour_by_dow`` reads the per-day ``agg_hour_daily`` (filtering dates by
DOW) on the fast path; ``agg_route_hour`` can't serve it (no date column). It
falls back to the live path under a ``service``/``routes`` filter, since that
table aggregates across all routes/services.
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta

from api.range import RangeCtx, build_updates_filter
from pipeline import perf
from pipeline.cache import async_lru_cache
from pipeline.reports.filters import _agg_filter, _dedup_cte, _time_band_sql_on


async def _latest_data_date(agency_id: int, ctx: RangeCtx, conn) -> date | None:
    """Most recent date inside ctx that has any samples.

    Used to anchor the headline's 7-day window to where data actually
    exists. Keeps the "this week vs last week" semantics meaningful
    when ingest is lagging or the user selects a wide historical range.

    Fast path (``ctx.time_band == 'all'``) reads ``agg_daily_trend`` for
    sub-millisecond response. Slow path (any other time band) falls back
    to live ``updates`` via the dedup CTE so the filter is honored.
    """
    if ctx.time_band == "all":
        where, params, _ = _agg_filter(ctx, next_param=2)
        where_clause = f" AND ({where})" if where else ""
        sql = f"SELECT MAX(date::date) AS d FROM agg_daily_trend WHERE agency_id=$1{where_clause}"
        row = await conn.fetchrow(sql, agency_id, *params)
        return row["d"] if row and row["d"] else None

    where, params, _ = build_updates_filter(ctx, next_param=2)
    sql = f"WITH {_dedup_cte(where)}\nSELECT MAX(date) AS d FROM deduped"
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

    Fast path (``ctx.time_band == 'all'``) reads ``agg_daily_trend`` as a
    sample-weighted average so days with more observations weigh
    proportionally. Slow path (any other time band) falls back to live
    ``updates`` via the dedup CTE so the hour-of-day filter is honored.
    """
    if ctx.time_band == "all":
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

    where, params, _ = build_updates_filter(ctx, next_param=2)
    sql = (
        f"WITH {_dedup_cte(where)}\n"
        "SELECT ROUND(AVG(dep_delay)/60.0::numeric, 2) AS avg_min,\n"
        "       COUNT(*) AS samples\n"
        "FROM deduped"
    )
    row = await conn.fetchrow(sql, agency_id, *params)
    avg = float(row["avg_min"]) if row["avg_min"] is not None else None
    return avg, int(row["samples"] or 0)


async def _per_route_avg(agency_id: int, ctx: RangeCtx, conn) -> dict[str, tuple[float, int]]:
    """Per-route avg_min + samples for ``ctx``. Keyed by route_code.

    Fast path (``ctx.time_band == 'all'``) reads ``agg_daily_trend`` with
    a sample-weighted average. Slow path falls back to live ``updates`` so
    the hour-of-day filter is honored.
    """
    if ctx.time_band == "all":
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

    where, params, _ = build_updates_filter(ctx, next_param=2)
    sql = (
        f"WITH {_dedup_cte(where)}\n"
        "SELECT route_code,\n"
        "       AVG(dep_delay)/60.0::numeric AS avg_min,\n"
        "       COUNT(*)::int AS samples\n"
        "FROM deduped\n"
        "GROUP BY route_code\n"
        "HAVING AVG(dep_delay) IS NOT NULL"
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
    buckets ending at ``ctx.to_date``. Honors DOW / service / routes.

    Fast path (``ctx.time_band == 'all'``) reads ``agg_daily_trend``. Slow
    path falls back to live ``updates`` via the dedup CTE so the
    hour-of-day filter is honored.
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
        if ctx.time_band == "all":
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
        else:
            where, params, n = build_updates_filter(window_ctx, next_param=2)
            rows = await conn.fetch(
                f"WITH {_dedup_cte(where)}\n"
                "SELECT route_code,\n"
                "       AVG(dep_delay)/60.0::numeric AS avg_min\n"
                "FROM deduped\n"
                f"WHERE route_code = ANY(${n}::text[])\n"
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
    """Top-20 routes by total positive delay contribution + rest share.

    Fast path (``ctx.time_band == 'all'``) reads ``agg_daily_trend`` and
    approximates ``SUM(GREATEST(dep_delay, 0))`` as
    ``SUM(GREATEST(avg_min, 0) * samples)`` per route, in minutes. Routes
    that ran early on net (negative ``avg_min``) contribute zero — same
    intent as the per-row metric: contribution to LATENESS, not the
    signed sum.

    Slow path (any non-default time band) falls back to live ``updates``
    and computes the per-row ``SUM(GREATEST(dep_delay, 0)) / 60`` exactly,
    so the hour-of-day filter is honored.
    """
    if ctx.time_band == "all":
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
    else:
        where, params, _ = build_updates_filter(ctx, next_param=2)
        rows = await conn.fetch(
            f"WITH {_dedup_cte(where)}\n"
            "SELECT route_code,\n"
            "       (SUM(GREATEST(dep_delay, 0)) / 60.0)::float AS total_late_min\n"
            "FROM deduped\n"
            "GROUP BY route_code\n"
            "ORDER BY total_late_min DESC NULLS LAST",
            agency_id,
            *params,
        )
    if not rows:
        return {"top_routes": [], "rest_share_pct": 0.0, "rest_route_count": 0}
    grand_total = sum(float(r["total_late_min"] or 0.0) for r in rows)
    if grand_total == 0:
        return {"top_routes": [], "rest_share_pct": 0.0, "rest_route_count": 0}
    top_n = rows[:20]
    codes = [r["route_code"] for r in top_n]
    names = await _route_short_names(agency_id, codes, conn)
    top_n_sum = sum(float(r["total_late_min"] or 0.0) for r in top_n)
    return {
        "top_routes": [
            {
                "route_code": r["route_code"],
                "route_short_name": names.get(r["route_code"]),
                "share_pct": round((float(r["total_late_min"] or 0.0) / grand_total) * 100.0, 1),
            }
            for r in top_n
        ],
        "rest_share_pct": round(((grand_total - top_n_sum) / grand_total) * 100.0, 1),
        "rest_route_count": max(len(rows) - len(top_n), 0),
    }


async def _top_delayed_routes(agency_id: int, cur_ctx: RangeCtx, conn, limit: int = 5) -> dict:
    """Routes ranked by absolute current-window avg delay ("routes to check
    now"), plus a count of routes at/above the DELAY_RAMP "not ok" threshold
    (2.0 min — frontend/src/styles/tokens.ts's ok/mild boundary).

    Uses cur_ctx (the same last-7-days-of-ctx window compute_overview_summary
    already builds for the headline), not the full ctx, so the KPI row's
    three stats and the routes list all describe the same snapshot.

    Fast path mirrors _concentration()'s: reads agg_daily_trend, but computes
    each route's true weighted average (SUM(avg_min*samples)/SUM(samples)),
    not _concentration()'s "total lateness contribution" sum — a route with
    few samples but a high average must outrank a route with more samples
    but a lower average, which _concentration()'s metric would get backwards.
    Slow path falls back to live updates for a non-default time_band, same
    as _concentration().
    """
    if cur_ctx.time_band == "all":
        where, params, _ = _agg_filter(cur_ctx, next_param=2)
        where_clause = f" AND ({where})" if where else ""
        rows = await conn.fetch(
            "SELECT route_code,\n"
            "       SUM(avg_min * samples)::float / NULLIF(SUM(samples), 0) AS avg_min\n"
            "FROM agg_daily_trend\n"
            f"WHERE agency_id=$1{where_clause}\n"
            "GROUP BY route_code\n"
            "HAVING SUM(samples) > 0\n"
            "ORDER BY avg_min DESC NULLS LAST",
            agency_id,
            *params,
        )
    else:
        where, params, _ = build_updates_filter(cur_ctx, next_param=2)
        rows = await conn.fetch(
            f"WITH {_dedup_cte(where)}\n"
            "SELECT route_code,\n"
            "       (AVG(dep_delay) / 60.0)::float AS avg_min\n"
            "FROM deduped\n"
            "GROUP BY route_code\n"
            "ORDER BY avg_min DESC NULLS LAST",
            agency_id,
            *params,
        )

    if not rows:
        return {"routes": [], "delayed_count": 0}

    delayed_count = sum(1 for r in rows if r["avg_min"] is not None and r["avg_min"] >= 2.0)
    top_n = rows[:limit]
    codes = [r["route_code"] for r in top_n]
    names = await _route_short_names(agency_id, codes, conn)
    return {
        "routes": [
            {
                "route_code": r["route_code"],
                "route_short_name": names.get(r["route_code"]),
                "avg_min": round(float(r["avg_min"]), 2),
            }
            for r in top_n
        ],
        "delayed_count": delayed_count,
    }


async def _peak_hour(agency_id: int, ctx: RangeCtx, conn) -> dict | None:
    """24-bucket avg by EXTRACT(HOUR FROM scheduled_time) + peak hour.

    Reads from ``agg_route_hour``, which is a fixed analyze-period rollup
    (no date column). Consequence: the date range and DOW in ``ctx`` are
    ignored — but ``service``, ``routes``, AND ``time_band`` all apply,
    because ``agg_route_hour`` does carry a TIME column.
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
    tb_frag, tb_params, n = _time_band_sql_on("scheduled_time", ctx.time_band, n)
    if tb_frag:
        parts.append(tb_frag)
        params.extend(tb_params)
    where_clause = (" AND " + " AND ".join(parts)) if parts else ""
    sql = (
        "SELECT EXTRACT(HOUR FROM scheduled_time)::int AS h,\n"
        "       (SUM(avg_min * samples) / NULLIF(SUM(samples), 0))::numeric AS avg_min\n"
        "FROM agg_route_hour\n"
        f"WHERE agency_id=$1{where_clause}\n"
        "GROUP BY EXTRACT(HOUR FROM scheduled_time)"
    )
    rows = await conn.fetch(sql, agency_id, *params)
    return _peak_from_hour_rows(rows)


async def _peak_hour_by_dow(agency_id: int, ctx: RangeCtx, conn, dow_group: str) -> dict | None:
    """24-hour avg delay restricted to weekday (``'weekday'``) or weekend
    (``'weekend'``) only.

    Fast path reads the per-day/hour ``agg_hour_daily`` (filtering dates by
    DOW), a sample-weighted average across the range — sub-second instead of
    the raw dedup scan that was ~96% of Overview's cold load. That table is
    aggregated across all routes/services, so a ``service``/``routes`` filter,
    or any ``time_band`` other than ``'all'``, falls back to the live path.
    """
    if ctx.time_band == "all" and ctx.service == "all" and not ctx.routes:
        dow_pred = "BETWEEN 1 AND 5" if dow_group == "weekday" else "IN (6, 7)"
        sql = (
            "SELECT hour AS h,\n"
            "       SUM(avg_min * samples) / NULLIF(SUM(samples), 0) AS avg_min\n"
            "FROM agg_hour_daily\n"
            "WHERE agency_id = $1 AND date >= ($2::text)::date AND date <= ($3::text)::date\n"
            f"  AND EXTRACT(ISODOW FROM date) {dow_pred}\n"
            "GROUP BY hour"
        )
        rows = await conn.fetch(sql, agency_id, str(ctx.from_date), str(ctx.to_date))
        return _peak_from_hour_rows(rows)

    overridden = RangeCtx(
        from_date=ctx.from_date,
        to_date=ctx.to_date,
        dow=dow_group,  # type: ignore[arg-type]
        time_band=ctx.time_band,
        service=ctx.service,
        routes=ctx.routes,
    )
    where, params, _ = build_updates_filter(overridden, next_param=2)
    sql = (
        f"WITH {_dedup_cte(where)}\n"
        "SELECT EXTRACT(HOUR FROM scheduled_time)::int AS h,\n"
        "       AVG(dep_delay)/60.0::numeric AS avg_min\n"
        "FROM deduped\n"
        "WHERE scheduled_time IS NOT NULL\n"
        "GROUP BY EXTRACT(HOUR FROM scheduled_time)"
    )
    rows = await conn.fetch(sql, agency_id, *params)
    return _peak_from_hour_rows(rows)


def _peak_from_hour_rows(rows) -> dict | None:
    """Shape ``(h, avg_min)`` rows into the ``by_hour[24]`` + peak payload."""
    if not rows:
        return None
    by_hour: list[float | None] = [None] * 24
    for r in rows:
        if r["avg_min"] is None:
            continue
        h = int(r["h"])
        if 0 <= h < 24:
            by_hour[h] = round(float(r["avg_min"]), 2)
    valid = [h for h in range(24) if by_hour[h] is not None]
    if not valid:
        return None
    peak_h = max(valid, key=lambda h: by_hour[h])  # type: ignore[arg-type, return-value]
    return {
        "by_hour": by_hour,
        "peak_hour": peak_h,
        "peak_avg_min": float(by_hour[peak_h]),  # type: ignore[arg-type]
    }


async def _service_split_daily(agency_id: int, ctx: RangeCtx, conn) -> list[dict]:
    """Per-day breakdown of 平日 vs 土日祝 avg delay over ``ctx``.

    Fast path (``ctx.time_band == 'all'``) reads ``agg_daily_trend`` with
    a sample-weighted per-(date, service_type) average. Slow path falls
    back to live ``updates`` so the hour-of-day filter is honored.

    Returns a list of ``{date: ISO str, weekday: float|None, weekend:
    float|None}`` rows sorted by date. Dates with neither service_type
    are silently dropped.
    """
    if ctx.time_band == "all":
        where, params, _ = _agg_filter(ctx, next_param=2)
        where_clause = f" AND ({where})" if where else ""
        sql = (
            "SELECT date, service_type,\n"
            "       (SUM(avg_min * samples) / NULLIF(SUM(samples), 0))::numeric AS avg\n"
            "FROM agg_daily_trend\n"
            f"WHERE agency_id=$1{where_clause}\n"
            "GROUP BY date, service_type\n"
            "ORDER BY date"
        )
    else:
        where, params, _ = build_updates_filter(ctx, next_param=2)
        sql = (
            f"WITH {_dedup_cte(where)}\n"
            "SELECT date, service_type,\n"
            "       AVG(dep_delay)/60.0::numeric AS avg\n"
            "FROM deduped\n"
            "GROUP BY date, service_type\n"
            "ORDER BY date"
        )
    rows = await conn.fetch(sql, agency_id, *params)
    by_date: dict[str, dict[str, float | None]] = {}
    for r in rows:
        d_raw = r["date"]
        d = d_raw if isinstance(d_raw, str) else d_raw.isoformat()
        st = r["service_type"]
        avg = float(r["avg"]) if r["avg"] is not None else None
        by_date.setdefault(d, {})[st] = avg
    out: list[dict] = []
    for d in sorted(by_date):
        bucket = by_date[d]
        out.append(
            {
                "date": d,
                "weekday": bucket.get("平日"),
                "weekend": bucket.get("土日祝"),
            }
        )
    return out


async def _movers(agency_id: int, cur_ctx: RangeCtx, base_ctx: RangeCtx, conn) -> dict:
    """Top-10 worsened + top-10 improved routes by signed delta_min.

    Compares ``cur_ctx`` against ``base_ctx`` (both built upstream by
    ``compute_overview_summary`` so the comparison is a true 7-day
    week-over-week regardless of the user's selected range). Requires
    >= 10 samples in BOTH windows for a route to enter the ranking — a
    route with a handful of obs can swing a huge delta_pct and would
    otherwise dominate top-3 with low statistical confidence.

    Frontend card variant slices :code:`.slice(0, 3)`; modal variant
    uses the full 10.
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
    # Partition by sign so "worse" only contains routes with positive
    # delta_min and "better" only routes with negative delta_min. With
    # the wider top-10 limit, sign-partitioning is the right way to
    # prevent the two lists from overlapping (a route can't both
    # improve and worsen at once).
    worse_all = [d for d in deltas if d[1] > 0]
    better_all = [d for d in deltas if d[1] < 0]
    worse = list(reversed(worse_all[-10:]))  # largest positive first
    better = better_all[:10]  # most-negative first
    codes = [c for c, _, _ in worse + better]
    names = await _route_short_names(agency_id, codes, conn)
    history = await _route_weekly_history(agency_id, codes, cur_ctx, conn, weeks_back=4)

    def _entry(code, dm, dp, direction):
        """Serialize one mover row (deltas, absolute averages, streak, sparkline)."""
        pts = [v for v in history.get(code, []) if v is not None]
        return {
            "route_code": code,
            "route_short_name": names.get(code),
            "delta_min": dm,
            "delta_pct": dp,
            # Absolute averages for both windows so the UI can show
            # "last week X min → this week Y min" instead of a bare Δ%.
            "current_avg_min": round(cur[code][0], 1),
            "previous_avg_min": round(prv[code][0], 1),
            "streak_weeks": _streak_weeks(history.get(code, []), direction=direction),
            "sparkline_points": pts,
        }

    return {
        "worse": [_entry(c, dm, dp, "up") for c, dm, dp in worse],
        "better": [_entry(c, dm, dp, "down") for c, dm, dp in better],
    }


async def _service_split(agency_id: int, ctx: RangeCtx, conn) -> dict[str, float]:
    """avg_min per service_type (typically '平日' / '土日祝').

    Fast path (``ctx.time_band == 'all'``) reads ``agg_daily_trend`` with
    a sample-weighted average. Slow path falls back to live ``updates``
    so the hour-of-day filter is honored.
    """
    if ctx.time_band == "all":
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
    else:
        where, params, _ = build_updates_filter(ctx, next_param=2)
        rows = await conn.fetch(
            f"WITH {_dedup_cte(where)}\n"
            "SELECT service_type,\n"
            "       AVG(dep_delay)/60.0::numeric AS avg_min\n"
            "FROM deduped\n"
            "GROUP BY service_type",
            agency_id,
            *params,
        )
    return {
        r["service_type"]: round(float(r["avg_min"]), 2) for r in rows if r["service_type"] and r["avg_min"] is not None
    }


async def _daily_sparkline(agency_id: int, ctx: RangeCtx, conn) -> list[float]:
    """Daily avg_min points (oldest first) over ``ctx``.

    Returns the FULL daily series. The frontend hero card slices the
    trailing 7 days for the inline sparkline; the modal variant uses the
    full series (typically 30+ points for a 30-day default range).

    Fast path (``ctx.time_band == 'all'``) reads ``agg_daily_trend`` with
    a sample-weighted average per date. Slow path falls back to live
    ``updates`` so the hour-of-day filter is honored.
    """
    if ctx.time_band == "all":
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
    else:
        where, params, _ = build_updates_filter(ctx, next_param=2)
        rows = await conn.fetch(
            f"WITH {_dedup_cte(where)}\n"
            "SELECT date AS day,\n"
            "       AVG(dep_delay)/60.0::numeric AS avg_min\n"
            "FROM deduped\n"
            "GROUP BY date\n"
            "ORDER BY date ASC",
            agency_id,
            *params,
        )
    pts = [round(float(r["avg_min"]), 2) for r in rows if r["avg_min"] is not None]
    return pts


@perf.timed("reports.overview")
@async_lru_cache(maxsize=64, ttl_seconds=300)
async def compute_overview_summary(
    agency_id: int,
    ctx: RangeCtx,
    conn,
    locale: str = "ja",
    *,
    pool=None,
) -> dict:
    """Build the 概況 payload for one agency over ``ctx``.

    Headline math uses the LAST 7 days of ``ctx`` and compares against
    the 7-day window immediately prior, so the "this week vs last week"
    copy is honest regardless of how the user has widened the ctx range.
    Concentration / peak / service_split / sparkline still aggregate over
    the full ctx to surface broader patterns.

    When ``pool`` is supplied (non-None), the ten stage queries are
    dispatched as concurrent asyncio tasks, each acquiring its own
    connection from the pool so they can truly run in parallel.  The two
    ``_peak_hour_by_dow`` calls — identified as 96 % of cold-load time in
    the baseline measurement — are the primary beneficiaries.  When
    ``pool`` is None (the default) the existing sequential path with
    per-stage timed_blocks is used unchanged, preserving behaviour for
    tests and ad-hoc callers.
    """
    async with perf.timed_block("overview.latest_date"):
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

    if pool is None:
        async with perf.timed_block("overview.headline"):
            avg_min, samples = await _headline_stats(agency_id, cur_ctx, conn)
            baseline_avg, _ = await _headline_stats(agency_id, base_ctx, conn)

        delta_min = None
        delta_pct = None
        if avg_min is not None and baseline_avg is not None:
            delta_min = round(avg_min - baseline_avg, 2)
            if baseline_avg != 0:
                delta_pct = round((delta_min / baseline_avg) * 100.0, 1)

        async with perf.timed_block("overview.movers"):
            movers = await _movers(agency_id, cur_ctx, base_ctx, conn)
        async with perf.timed_block("overview.concentration"):
            concentration = await _concentration(agency_id, ctx, conn)
        async with perf.timed_block("overview.top_delayed"):
            top_delayed = await _top_delayed_routes(agency_id, cur_ctx, conn)
        async with perf.timed_block("overview.peaks"):
            peak = await _peak_hour(agency_id, ctx, conn)
            peak_weekday = await _peak_hour_by_dow(agency_id, ctx, conn, "weekday")
            peak_weekend = await _peak_hour_by_dow(agency_id, ctx, conn, "weekend")
        async with perf.timed_block("overview.service_split"):
            service_split = await _service_split(agency_id, ctx, conn)
            service_split_daily = await _service_split_daily(agency_id, ctx, conn)
        async with perf.timed_block("overview.sparkline"):
            # Hero card slices `.slice(-7)`; modal shows full series.
            sparkline_points = await _daily_sparkline(agency_id, ctx, conn)

    else:
        # Pool-gather path — each task acquires its own pooled connection
        # so all ten queries can run concurrently. A single asyncpg
        # connection cannot multiplex queries; pool.acquire() queues when
        # saturated, so concurrency is naturally bounded by pool size.
        # No per-stage timed_blocks here; the top-level reports.overview
        # label captures the wall-clock total.
        async def _own_conn(fn, *args):
            """Acquire a pool connection, call ``fn(*args, conn)``, release."""
            async with pool.acquire() as c:
                return await fn(*args, c)

        async def _peak_dow(group: str) -> dict | None:
            """Acquire a pool connection and run ``_peak_hour_by_dow`` for ``group``."""
            async with pool.acquire() as c:
                return await _peak_hour_by_dow(agency_id, ctx, c, group)

        (
            (avg_min, samples),
            (baseline_avg, _),
            movers,
            concentration,
            top_delayed,
            peak,
            peak_weekday,
            peak_weekend,
            service_split,
            service_split_daily,
            sparkline_points,
        ) = await asyncio.gather(
            _own_conn(_headline_stats, agency_id, cur_ctx),
            _own_conn(_headline_stats, agency_id, base_ctx),
            _own_conn(_movers, agency_id, cur_ctx, base_ctx),
            _own_conn(_concentration, agency_id, ctx),
            _own_conn(_top_delayed_routes, agency_id, cur_ctx),
            _own_conn(_peak_hour, agency_id, ctx),
            _peak_dow("weekday"),
            _peak_dow("weekend"),
            _own_conn(_service_split, agency_id, ctx),
            _own_conn(_service_split_daily, agency_id, ctx),
            _own_conn(_daily_sparkline, agency_id, ctx),
        )

        delta_min = None
        delta_pct = None
        if avg_min is not None and baseline_avg is not None:
            delta_min = round(avg_min - baseline_avg, 2)
            if baseline_avg != 0:
                delta_pct = round((delta_min / baseline_avg) * 100.0, 1)

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
        "top_delayed": top_delayed,
        "peak_hour": peak,
        "peak_hour_weekday": peak_weekday,
        "peak_hour_weekend": peak_weekend,
        "service_split": service_split,
        "service_split_daily": service_split_daily,
        "sparkline_points": sparkline_points,
    }
