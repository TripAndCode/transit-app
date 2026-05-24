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

from api.range import RangeCtx, build_updates_filter
from pipeline.cache import async_lru_cache
from pipeline.db import build_dedup_inner_sql


def _dedup_cte(where_frag: str) -> str:
    """Wrap the shared latest-by-captured_at dedup SQL in a `deduped` CTE.

    `where_frag` is a trusted server-built fragment from
    `api.range.build_updates_filter` — never user input.
    """
    return f"deduped AS ({build_dedup_inner_sql(placeholder='$1', extra_where=where_frag)})"


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


def _shift_ctx_one_week_back(ctx: RangeCtx) -> RangeCtx:
    """Return a RangeCtx whose dates are shifted 7 days earlier.

    Preserves dow / time_band / service / routes filters so the baseline
    is service-day-aware via composition with build_updates_filter.
    """
    return RangeCtx(
        from_date=ctx.from_date - timedelta(days=7),
        to_date=ctx.to_date - timedelta(days=7),
        dow=ctx.dow,
        time_band=ctx.time_band,
        service=ctx.service,
        routes=ctx.routes,
    )


async def _headline_stats(agency_id: int, ctx: RangeCtx, conn) -> tuple[float | None, int]:
    """Return (avg_min, samples) for the headline over ``ctx``."""
    where_frag, params, _ = build_updates_filter(ctx, next_param=2)
    sql = (
        "SELECT ROUND(AVG(dep_delay)/60.0::numeric, 2) AS avg_min, "
        "       COUNT(*) AS samples "
        "FROM updates "
        f"WHERE agency_id=$1 AND dep_delay IS NOT NULL AND ({where_frag})"
    )
    row = await conn.fetchrow(sql, agency_id, *params)
    avg = float(row["avg_min"]) if row["avg_min"] is not None else None
    return avg, int(row["samples"] or 0)


async def _per_route_avg(agency_id: int, ctx: RangeCtx, conn) -> dict[str, tuple[float, int]]:
    """Per-route avg_min + samples for ``ctx``. Keyed by route_code."""
    where_frag, params, _ = build_updates_filter(ctx, next_param=2)
    sql = (
        "SELECT route_code, "
        "       AVG(dep_delay)/60.0::numeric AS avg_min, "
        "       COUNT(*) AS samples "
        "FROM updates "
        f"WHERE agency_id=$1 AND dep_delay IS NOT NULL AND ({where_frag}) "
        "GROUP BY route_code"
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
    """Per-route weekly avg_min for the last ``weeks_back`` weeks ending
    at ``ctx.to_date``. Returns oldest -> newest list; missing weeks are None."""
    if not route_codes:
        return {}
    windows: list[tuple[date, date]] = []
    span = (ctx.to_date - ctx.from_date).days
    for k in range(weeks_back - 1, -1, -1):
        end = ctx.to_date - timedelta(days=7 * k)
        start = end - timedelta(days=span)
        windows.append((start, end))

    out: dict[str, list[float | None]] = {code: [] for code in route_codes}
    for start, end in windows:
        rows = await conn.fetch(
            "SELECT route_code, AVG(dep_delay)/60.0::numeric AS avg_min "
            "FROM updates "
            "WHERE agency_id=$1 AND dep_delay IS NOT NULL "
            "  AND captured_at::date BETWEEN $2 AND $3 "
            "  AND route_code = ANY($4::text[]) "
            "GROUP BY route_code",
            agency_id, start, end, list(route_codes),
        )
        wk_map = {r["route_code"]: float(r["avg_min"]) for r in rows}
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


async def _movers(agency_id: int, ctx: RangeCtx, conn) -> dict:
    """Top-3 worsened + top-3 improved routes by signed delta_min."""
    cur = await _per_route_avg(agency_id, ctx, conn)
    prv = await _per_route_avg(agency_id, _shift_ctx_one_week_back(ctx), conn)
    common = set(cur) & set(prv)
    deltas: list[tuple[str, float, float]] = []
    for code in common:
        cur_avg, _ = cur[code]
        prv_avg, _ = prv[code]
        if prv_avg == 0:
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
    history = await _route_weekly_history(agency_id, codes, ctx, conn, weeks_back=4)

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


async def compute_overview_summary(
    agency_id: int,
    ctx: RangeCtx,
    conn,
    locale: str = "ja",
) -> dict:
    """Build the 概況 payload for one agency over ``ctx``.

    See ``docs/superpowers/specs/2026-05-25-overview-tab-design.md``.
    Each sub-section is a separate helper (added in tasks T2-T8).
    """
    avg_min, samples = await _headline_stats(agency_id, ctx, conn)
    baseline_avg, _ = await _headline_stats(agency_id, _shift_ctx_one_week_back(ctx), conn)

    delta_min = None
    delta_pct = None
    if avg_min is not None and baseline_avg is not None:
        delta_min = round(avg_min - baseline_avg, 2)
        if baseline_avg != 0:
            delta_pct = round((delta_min / baseline_avg) * 100.0, 1)

    movers = await _movers(agency_id, ctx, conn)

    return {
        "headline": {
            "avg_min": avg_min,
            "baseline_avg_min": baseline_avg,
            "delta_min": delta_min,
            "delta_pct": delta_pct,
            "samples": samples,
        },
        "movers": movers,
        "concentration": {"top_routes": [], "rest_share_pct": 0.0},
        "peak_hour": None,
        "service_split": {},
        "sparkline_points": [],
    }
