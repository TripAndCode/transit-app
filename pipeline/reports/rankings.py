"""The seven report-tab compute_* functions (ranking, on-time, trend, etc.)."""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from api.range import RangeCtx, ServiceType, build_updates_filter_ch, resolve_service_dates, selected_service_dates
from pipeline import perf
from pipeline.cache import async_lru_cache
from pipeline.histogram import (
    LEGACY_ON_TIME_LATE_TOLERANCE_SEC,
    LEGACY_PRESET_NAME,
    LEGACY_SEVERE_LATE_TOLERANCE_SEC,
    count_in_range,
    percentile_from_hist,
)
from pipeline.reports.filters import (
    _MIN,
    _agg_filter,
    _ch_rows,
    _dedup_cte_ch,
    _dist_filter,
    _round2,
    _service_label,
)
from pipeline.service_day import service_date_ch

# Reports read the precomputed per-day distribution (agg_route_daily_dist) and
# sum across the range. The aggregate has no hour-of-day column, so a time_band
# filter can't be served from it — those queries fall back to the live scan.

# Named on-time/late tolerance presets exposed to API callers (see
# api/routers/reports.py). "legacy_60s" is the only preset today: it is an
# explicit, opt-in spelling of "don't override the tolerance" (compute_on_time/
# compute_worst_5min already default to it), not a distinct code path — see
# both functions' own docstrings for why that distinction matters (only the
# untouched default reads the exact analyze()-time column; ANY explicit
# tolerance, even one numerically equal to the legacy value, reads the
# approximate histogram instead).
ON_TIME_PRESETS: dict[str, tuple[int | None, int | None]] = {
    LEGACY_PRESET_NAME: (None, None),
}


# The fewest observations behind a route compute_ranking ranks: percentiles
# over fewer say nothing.
MIN_GROUP_SAMPLES = 21
# The reports tab's ranking floor. A route observed a few dozen times in the
# range would otherwise top a ranking of routes observed hundreds of times.
RANKING_MIN_SAMPLES = 100


def _avg_min(sum_delay_sec: int, samples: int) -> Decimal:
    """Mean delay in minutes, 2 dp — matches live ROUND(AVG(dep_delay)/60, 2).

    ROUND_HALF_UP mirrors Postgres ROUND (half away from zero); Decimal's
    default ROUND_HALF_EVEN would diverge at exact-half 2-dp boundaries.
    """
    return (Decimal(sum_delay_sec) / samples / 60).quantize(_MIN, rounding=ROUND_HALF_UP)


def _sec_to_min(sec: float | None) -> Decimal | None:
    """Seconds → 2-dp minutes, or None for an empty histogram."""
    return None if sec is None else (Decimal(sec) / 60).quantize(_MIN, rounding=ROUND_HALF_UP)


def _round1(x: float) -> Decimal:
    """Round a float to 1 dp, half-up — matches Postgres ``ROUND(x::numeric, 1)``.
    Same rationale as :func:`_round2`."""
    return Decimal(str(x)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def _weighted_avg_min(days: list[dict]) -> float | None:
    """Sample-weighted mean delay across trend buckets — the pooled mean (approximate).

    Each `days` entry is already a per-bucket sample-weighted mean (rounded to
    2 dp, so the pooled figure is approximate) with a
    `samples` weight, so a plain mean-of-means would overweight thin days (one
    sparse outlier day could dominate the headline). Null-`avg_min` days are
    skipped (not counted as 0). Returns None when there are no measured samples.
    """
    num = 0.0
    den = 0
    for d in days:
        v = d.get("avg_min")
        if v is None:
            continue
        s = d.get("samples") or 0
        num += v * s
        den += s
    return num / den if den else None


async def _read_dist_scalars(agency_id: int, ctx: RangeCtx, conn) -> list:
    """Range-scan agg_route_daily_dist, summing the exact scalars per route.

    The sums run across every date and every raw service id of a route (see
    :func:`_service_label`). Used by on_time / worst_5min (no percentiles
    needed). DOW/service/route filters apply; time_band is the caller's
    responsibility (see module note).
    """
    where, params, _ = _dist_filter(ctx, next_param=2)
    sql = (
        "SELECT route_code,\n"
        "       SUM(samples) AS samples,\n"
        "       SUM(sum_delay_sec) AS sum_delay_sec,\n"
        "       SUM(on_time_count) AS on_time_count,\n"
        "       SUM(late5_count) AS late5_count\n"
        "FROM agg_route_daily_dist\n"
        f"WHERE agency_id = $1 AND {where}\n"
        "GROUP BY route_code"
    )
    return await conn.fetch(sql, agency_id, *params)


async def _read_dist_with_hist(agency_id: int, ctx: RangeCtx, conn) -> list:
    """Range-scan agg_route_daily_dist per route, summing scalars AND merging histograms.

    Rows of every date and raw service id of a route merge into one (see
    :func:`_service_label`). The histograms are summed element-wise (unnest
    WITH ORDINALITY → regroup → array_agg) so p50/p90 can be interpolated from
    the merged buckets in Python. The exact over-5-minute count comes along,
    for a caller that estimates only the on-time count from the histogram.
    """
    where, params, _ = _dist_filter(ctx, next_param=2)
    sql = (
        "WITH ranged AS (\n"
        "    SELECT route_code, samples, sum_delay_sec, late5_count, hist\n"
        "    FROM agg_route_daily_dist\n"
        f"    WHERE agency_id = $1 AND {where}\n"
        "),\n"
        "merged_hist AS (\n"
        "    SELECT route_code, i, SUM(h) AS c\n"
        "    FROM ranged, unnest(hist) WITH ORDINALITY u(h, i)\n"
        "    GROUP BY route_code, i\n"
        "),\n"
        "hists AS (\n"
        "    SELECT route_code, array_agg(c ORDER BY i) AS hist\n"
        "    FROM merged_hist GROUP BY route_code\n"
        "),\n"
        "scalars AS (\n"
        "    SELECT route_code,\n"
        "           SUM(samples) AS samples, SUM(sum_delay_sec) AS sum_delay_sec,\n"
        "           SUM(late5_count) AS late5_count\n"
        "    FROM ranged GROUP BY route_code\n"
        ")\n"
        "SELECT s.route_code, s.samples, s.sum_delay_sec, s.late5_count, h.hist\n"
        "FROM scalars s JOIN hists h USING (route_code)"
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
    limit: int | None = 100,
    min_samples: int = MIN_GROUP_SAMPLES,
) -> list[tuple]:
    """Routes ranked by average delay over ctx. ``sort_order='asc'`` → best first.

    Reads agg_route_daily_dist: avg/samples are exact; p50/p90 are interpolated
    from the merged delay histogram (approximate within one bucket — fine for
    ranking). A time_band filter falls back to the live scan (ClickHouse).
    Each row is one route, ``(route_code, service, avg_min, p50_min, p90_min,
    samples)`` with ``service`` from :func:`_service_label`. Routes with fewer
    than ``min_samples`` observations are left out; ``limit=None`` returns
    every remaining route.
    """
    if ctx.time_band != "all":
        if ch is None:
            raise RuntimeError("compute_ranking's time_band-filtered live fallback requires a ClickHouse client")
        return await _ranking_live(agency_id, ctx, conn, ch, sort_order, limit, min_samples)

    rows = await _read_dist_with_hist(agency_id, ctx, conn)
    service = _service_label(ctx)
    out: list[tuple] = []
    for r in rows:
        samples = r["samples"]
        if samples < min_samples:
            continue
        out.append(
            (
                r["route_code"],
                service,
                _avg_min(r["sum_delay_sec"], samples),
                _sec_to_min(percentile_from_hist(r["hist"], 0.5)),
                _sec_to_min(percentile_from_hist(r["hist"], 0.9)),
                samples,
            )
        )
    # avg_min is element 2; None never occurs (samples >= MIN_GROUP_SAMPLES), so plain sort.
    # Two-pass stable sort: pre-sort by route_code (element 0) so ties on
    # avg_min break deterministically in ascending route_code order
    # regardless of `reverse` — `rows` comes from a GROUP BY with no ordering
    # guarantee, and an arbitrary tie order would make the ranking unstable
    # run to run.
    out.sort(key=lambda t: t[0])
    out.sort(key=lambda t: t[2], reverse=sort_order.lower() == "desc")
    return out if limit is None else out[:limit]


async def _ranking_live(
    agency_id: int, ctx: RangeCtx, conn, ch, sort_order: str, limit: int | None, min_samples: int
) -> list[tuple]:
    """Live raw-scan ranking — fallback for time_band-filtered queries.

    p50/p90 are computed via `rank()`/`count()` window functions reproducing
    a min-rank-tie `PERCENT_RANK()` formula — NOT ClickHouse's
    `quantileExact`, which is a pure positional pick (`sorted[floor(q*n)]`),
    and not the fast path's histogram interpolation (`percentile_from_hist`
    over `agg_route_daily_dist`). This is a known, accepted divergence: this
    ClickHouse fallback can disagree with the fast path on tied/clustered
    `dep_delay` data (common: exact-zero delays and clamped/rounded values
    dominate this column), since the two paths use different tie-handling
    rules. E.g. sorted `[0]*95 + [600]*5`: this function's p90 is 600s, and
    `quantileExact`'s would be 0s. Every group here has
    at least `min_samples` rows (the HAVING gate), so `avg` is never NULL/NaN
    — no empty-input guard needed.
    """
    cte_sql, ch_params = _dedup_cte_ch(ctx)
    order = "DESC" if sort_order.lower() == "desc" else "ASC"
    params = {"agency_id": agency_id, "rk_min": min_samples, **ch_params}
    limit_sql = _limit_clause(params, limit, "rk_limit")
    result = await ch.query(
        f"WITH {cte_sql},\n"
        "deduped_ranked AS (\n"
        "    SELECT route_code, dep_delay,\n"
        "        (rank() OVER (PARTITION BY route_code ORDER BY dep_delay) - 1)\n"
        "        / (count() OVER (PARTITION BY route_code) - 1) AS pct\n"
        "    FROM deduped\n"
        ")\n"
        "SELECT route_code,\n"
        "       avg(dep_delay) / 60.0 AS avg_min,\n"
        "       minIf(dep_delay, pct >= 0.5) / 60.0 AS p50_min,\n"
        "       minIf(dep_delay, pct >= 0.9) / 60.0 AS p90_min,\n"
        "       count(*) AS samples\n"
        "FROM deduped_ranked\n"
        "GROUP BY route_code\n"
        "HAVING count(*) >= {rk_min:UInt32}\n"
        f"ORDER BY avg_min {order}, route_code" + limit_sql,
        parameters=params,
    )
    # ClickHouse's round() is round-half-to-even; round in Python (half-up)
    # to match Postgres ROUND() and the agg fast path's _avg_min/_sec_to_min.
    # p50/p90 can legitimately be None: this function's min-rank tie handling
    # gives every row in an all-tied (or heavily-tied) partition the SAME
    # rank, so `minIf(dep_delay, pct >= q)` matches zero rows once every
    # row's pct sits below q. compute_ranking's aggregate path has no such
    # failure mode (percentile_from_hist resolves for any non-empty
    # histogram) — this NULL case is specific to this ClickHouse
    # fallback's min-rank tie formula.
    service = _service_label(ctx)
    return [
        (
            route_code,
            service,
            _round2(avg_min),
            _round2(p50_min) if p50_min is not None else None,
            _round2(p90_min) if p90_min is not None else None,
            samples,
        )
        for route_code, avg_min, p50_min, p90_min, samples in result.result_rows
    ]


@perf.timed("reports.on_time")
@async_lru_cache(maxsize=64, ttl_seconds=300)
async def compute_on_time(
    agency_id: int,
    ctx: RangeCtx,
    conn,
    ch=None,
    threshold_sec: int = 60,
    limit: int | None = 100,
    sort_order: str = "desc",
    early_tolerance_sec: int | None = None,
    late_tolerance_sec: int | None = None,
) -> list[tuple]:
    """On-time percentage per route, one row per route (see :func:`_service_label`).

    ``sort_order='desc'`` returns best on-time routes first (highest %);
    ``sort_order='asc'`` returns worst routes first (lowest %).
    ``limit=None`` returns every route with more than 20 observations.

    ``early_tolerance_sec``/``late_tolerance_sec`` generalize the on-time
    window to ``-early_tolerance_sec <= dep_delay <= late_tolerance_sec`` (an
    unboundedly-early departure still counts as on-time when
    ``early_tolerance_sec`` is left ``None`` — the LEGACY_60S preset's own
    behavior, unchanged). Leaving BOTH unset (the default, ``ON_TIME_PRESETS
    [LEGACY_PRESET_NAME]``) is byte-identical to the historical behavior:
    ``on_time_count`` (<=60s, baked in at analyze time) is read exactly, and
    ``threshold_sec``/``time_band`` still control the pre-existing live
    ClickHouse fallback for that path. Passing EITHER tolerance — even a
    value numerically equal to the legacy default — opts into a query-time
    estimate from agg_route_daily_dist's merged delay histogram instead (see
    :func:`pipeline.histogram.count_in_range`; bounded to one histogram
    bucket, i.e. ``pipeline.histogram.WIDTH`` seconds, of error), so a custom
    on-time definition still avoids both a raw-``updates`` scan and an
    analyze() re-aggregation. A time_band filter still needs the live scan
    either way (the histogram has no hour-of-day dimension).

    Returned percentages carry no confidence signal of their own -- a caller
    displaying one of these percentages to a user should also call
    :func:`pipeline.stats.annotate_on_time_pct_confidence` (see
    api/routers/reports.py's ``on_time`` report and pipeline/query/tools.py's
    ``on_time_rate`` tool for the existing pattern) rather than showing a
    thin-sample percentage with the same visual weight as a well-supported
    one.
    """
    if early_tolerance_sec is None and late_tolerance_sec is None:
        if ctx.time_band != "all" or threshold_sec != LEGACY_ON_TIME_LATE_TOLERANCE_SEC:
            if ch is None:
                raise RuntimeError(
                    "compute_on_time's time_band/threshold-filtered live fallback requires a ClickHouse client"
                )
            return await _on_time_live(agency_id, ctx, conn, ch, threshold_sec, limit, sort_order)

        rows = await _read_dist_scalars(agency_id, ctx, conn)
        service = _service_label(ctx)
        out: list[tuple] = []
        for r in rows:
            samples = r["samples"]
            if samples <= 20:
                continue
            on_time_pct = (Decimal(r["on_time_count"]) * 100 / samples).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
            out.append(
                (
                    r["route_code"],
                    service,
                    on_time_pct,
                    _avg_min(r["sum_delay_sec"], samples),
                    samples,
                )
            )
        # Two-pass stable sort: route_code (element 0) tie-breaks ties on
        # on_time_pct in ascending order regardless of `reverse` — same
        # unordered-GROUP-BY source as compute_ranking above.
        out.sort(key=lambda t: t[0])
        out.sort(key=lambda t: t[2], reverse=sort_order.lower() == "desc")
        return out if limit is None else out[:limit]

    # Custom on-time window: estimate from the merged histogram at query time.
    low_sec = None if early_tolerance_sec is None else -early_tolerance_sec
    high_sec = LEGACY_ON_TIME_LATE_TOLERANCE_SEC if late_tolerance_sec is None else late_tolerance_sec
    if ctx.time_band != "all":
        if ch is None:
            raise RuntimeError("compute_on_time's time_band-filtered live fallback requires a ClickHouse client")
        return await _on_time_live_window(agency_id, ctx, conn, ch, low_sec, high_sec, limit, sort_order)

    rows = await _read_dist_with_hist(agency_id, ctx, conn)
    service = _service_label(ctx)
    out = []
    for r in rows:
        samples = r["samples"]
        if samples <= 20:
            continue
        on_time_est = count_in_range(r["hist"], low_sec, high_sec)
        on_time_pct = (Decimal(str(on_time_est)) * 100 / samples).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        out.append(
            (
                r["route_code"],
                service,
                on_time_pct,
                _avg_min(r["sum_delay_sec"], samples),
                samples,
            )
        )
    out.sort(key=lambda t: t[0])
    out.sort(key=lambda t: t[2], reverse=sort_order.lower() == "desc")
    return out if limit is None else out[:limit]


def _limit_clause(params: dict, limit: int | None, name: str) -> str:
    """A ``LIMIT`` clause bound into ``params`` as ``name``, or none for every row."""
    if limit is None:
        return ""
    params[name] = limit
    return f"\nLIMIT {{{name}:UInt32}}"


async def _on_time_live(
    agency_id: int, ctx: RangeCtx, conn, ch, threshold_sec: int, limit: int | None, sort_order: str
) -> list[tuple]:
    """Live raw-scan on-time — fallback for time_band / custom-threshold queries.

    Every group here has > 20 rows (the HAVING gate), so `avg`/`sum` are
    never NULL/NaN — no empty-input guard needed.
    """
    cte_sql, ch_params = _dedup_cte_ch(ctx)
    order = "DESC" if sort_order.lower() == "desc" else "ASC"
    params = {"agency_id": agency_id, "ot_threshold": threshold_sec, **ch_params}
    limit_sql = _limit_clause(params, limit, "ot_limit")
    result = await ch.query(
        f"WITH {cte_sql}\n"
        "SELECT route_code,\n"
        "       sum(CASE WHEN dep_delay <= {ot_threshold:Int32} THEN 1.0 ELSE 0 END)\n"
        "           * 100.0 / count(*) AS on_time_pct,\n"
        "       avg(dep_delay) / 60.0 AS avg_min,\n"
        "       count(*) AS samples\n"
        "FROM deduped\n"
        "GROUP BY route_code\n"
        "HAVING count(*) > 20\n"
        f"ORDER BY on_time_pct {order}, route_code" + limit_sql,
        parameters=params,
    )
    # Round in Python (half-up) to match Postgres ROUND() — see _ranking_live.
    service = _service_label(ctx)
    return [
        (route_code, service, _round1(on_time_pct), _round2(avg_min), samples)
        for route_code, on_time_pct, avg_min, samples in result.result_rows
    ]


async def _on_time_live_window(
    agency_id: int,
    ctx: RangeCtx,
    conn,
    ch,
    low_sec: int | None,
    high_sec: int,
    limit: int | None,
    sort_order: str,
) -> list[tuple]:
    """Live raw-scan on-time window — fallback for time_band + custom-tolerance
    queries, where neither the exact ``on_time_count`` column (single fixed
    threshold) nor the merged histogram (no hour-of-day dimension) can serve
    the request. ``low_sec=None`` means unbounded below, matching
    :func:`compute_on_time`'s own early_tolerance_sec=None convention.

    Every group here has > 20 rows (the HAVING gate), so `avg` is never NaN.
    """
    cte_sql, ch_params = _dedup_cte_ch(ctx)
    order = "DESC" if sort_order.lower() == "desc" else "ASC"
    low_clause = "1=1" if low_sec is None else "dep_delay >= {ot_low:Int32}"
    params = {
        "agency_id": agency_id,
        "ot_high": high_sec,
        "ot_low": 0 if low_sec is None else low_sec,
        **ch_params,
    }
    limit_sql = _limit_clause(params, limit, "ot_limit")
    result = await ch.query(
        f"WITH {cte_sql}\n"
        "SELECT route_code,\n"
        f"       sum(CASE WHEN dep_delay <= {{ot_high:Int32}} AND {low_clause} THEN 1.0 ELSE 0 END)\n"
        "           * 100.0 / count(*) AS on_time_pct,\n"
        "       avg(dep_delay) / 60.0 AS avg_min,\n"
        "       count(*) AS samples\n"
        "FROM deduped\n"
        "GROUP BY route_code\n"
        "HAVING count(*) > 20\n"
        f"ORDER BY on_time_pct {order}, route_code" + limit_sql,
        parameters=params,
    )
    # Round in Python (half-up) to match Postgres ROUND() — see _ranking_live.
    service = _service_label(ctx)
    return [
        (route_code, service, _round1(on_time_pct), _round2(avg_min), samples)
        for route_code, on_time_pct, avg_min, samples in result.result_rows
    ]


@perf.timed("reports.worst_5min")
@async_lru_cache(maxsize=64, ttl_seconds=300)
async def compute_worst_5min(
    agency_id: int,
    ctx: RangeCtx,
    conn,
    ch=None,
    limit: int = 100,
    late_tolerance_sec: int | None = None,
) -> list[tuple]:
    """Routes ranked by count of severely-late observations.

    Reads agg_route_daily_dist (``late5_count`` is exact, >300s baked in at
    analyze time — the LEGACY_60S preset's own severe-late cutoff). Leaving
    ``late_tolerance_sec`` unset (the default) is byte-identical to that
    historical behavior. Passing an explicit value — even ``300`` itself —
    opts into a query-time estimate from the merged delay histogram instead
    (see :func:`pipeline.histogram.count_in_range`; bounded to one histogram
    bucket of error), rather than needing an analyze() re-aggregation for a
    non-default severity cutoff. A time_band filter still falls back to the
    live scan (ClickHouse) either way — the histogram has no hour-of-day
    dimension, and the exact column has none either.
    """
    late = LEGACY_SEVERE_LATE_TOLERANCE_SEC if late_tolerance_sec is None else late_tolerance_sec
    if ctx.time_band != "all":
        if ch is None:
            raise RuntimeError("compute_worst_5min's time_band-filtered live fallback requires a ClickHouse client")
        return await _worst_5min_live(agency_id, ctx, conn, ch, limit, late)

    service = _service_label(ctx)
    if late_tolerance_sec is None:
        rows = await _read_dist_scalars(agency_id, ctx, conn)
        out: list[tuple] = []
        for r in rows:
            late5 = r["late5_count"]
            if late5 <= 0:  # mirror live HAVING SUM(...) > 0
                continue
            out.append(
                (
                    r["route_code"],
                    service,
                    late5,
                    _avg_min(r["sum_delay_sec"], r["samples"]),
                    r["samples"],
                )
            )
        # Two-pass stable sort: route_code (element 0) tie-breaks ties on
        # late5_count in ascending order — same unordered-GROUP-BY source as
        # compute_ranking above.
        out.sort(key=lambda t: t[0])
        out.sort(key=lambda t: t[2], reverse=True)
        return out[:limit]

    # Custom severe-late cutoff: estimate from the merged histogram at query time.
    rows = await _read_dist_with_hist(agency_id, ctx, conn)
    out = []
    for r in rows:
        samples = r["samples"]
        late5_est = samples - count_in_range(r["hist"], None, late)
        # ROUND_HALF_UP, not the built-in round()'s banker's rounding — same
        # rationale as _avg_min/_round1 above, applied to this estimate's
        # nearest-integer count.
        late5 = int(Decimal(str(late5_est)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        if late5 <= 0:
            continue
        out.append(
            (
                r["route_code"],
                service,
                late5,
                _avg_min(r["sum_delay_sec"], samples),
                samples,
            )
        )
    out.sort(key=lambda t: t[0])
    out.sort(key=lambda t: t[2], reverse=True)
    return out[:limit]


async def _worst_5min_live(agency_id: int, ctx: RangeCtx, conn, ch, limit: int, late_tolerance_sec: int) -> list[tuple]:
    """Live raw-scan worst-5min — fallback for time_band-filtered queries."""
    cte_sql, ch_params = _dedup_cte_ch(ctx)
    result = await ch.query(
        f"WITH {cte_sql}\n"
        "SELECT route_code,\n"
        "       sum(CASE WHEN dep_delay > {w5_threshold:Int32} THEN 1 ELSE 0 END) AS late5_count,\n"
        "       avg(dep_delay) / 60.0 AS avg_min,\n"
        "       count(*) AS samples\n"
        "FROM deduped\n"
        "GROUP BY route_code\n"
        "HAVING sum(CASE WHEN dep_delay > {w5_threshold:Int32} THEN 1 ELSE 0 END) > 0\n"
        "ORDER BY late5_count DESC, route_code\n"
        "LIMIT {w5_limit:UInt32}",
        parameters={
            "agency_id": agency_id,
            "w5_threshold": late_tolerance_sec,
            "w5_limit": limit,
            **ch_params,
        },
    )
    # Round in Python (half-up) to match Postgres ROUND() — see _ranking_live.
    service = _service_label(ctx)
    return [
        (route_code, service, late5_count, _round2(avg_min), samples)
        for route_code, late5_count, avg_min, samples in result.result_rows
    ]


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
    """Worst-delay routes over the agency's 平日 or 土日祝 days.

    ``dow_group`` ``'weekday'`` keeps the dates the agency's calendar runs as
    平日, ``'weekend'`` every other date: a public holiday on a weekday, or a
    weekday run on the holiday timetable, counts as 土日祝 (:func:`_day_type_ctx`).
    That day type replaces the user's ``dow`` and ``service`` filters, which
    could only narrow it to a different question, so each row's service slot
    is None and its day-type slot names the side. ``routes`` and
    ``time_band`` are kept.
    """
    if ctx.time_band != "all" and ch is None:
        raise RuntimeError("compute_dow_ranking's time_band-filtered live fallback requires a ClickHouse client")
    label = _DAY_TYPE_SIDES[dow_group]
    overridden = await _day_type_ctx(agency_id, ctx, conn, label)
    if ctx.time_band != "all":
        return await _dow_ranking_live(agency_id, overridden, ch, label, limit)

    # agg_daily_trend filtered to the day type's dates, sample-weighted.
    where, params, n = _agg_filter(overridden, next_param=2)
    sql = (
        f"SELECT route_code, '{label}' AS dow,\n"
        # A count beside a sum_delay_sec-derived average counts that average's
        # rows, so all three FILTER alike — matching _dow_ranking_live, whose
        # count(*) covers exactly the rows its own average does.
        # compute_on_time's on_time_count/samples stays unfiltered: that ratio
        # does not condition on sum_delay_sec.
        "       ROUND((SUM(sum_delay_sec) FILTER (WHERE sum_delay_sec IS NOT NULL)::numeric\n"
        "           / NULLIF(SUM(samples) FILTER (WHERE sum_delay_sec IS NOT NULL), 0) / 60.0), 2) AS avg_min,\n"
        "       COALESCE(SUM(samples) FILTER (WHERE sum_delay_sec IS NOT NULL), 0)::int AS samples\n"
        "FROM agg_daily_trend\n"
        f"WHERE agency_id = $1 AND {where}\n"
        "GROUP BY route_code\n"
        "HAVING SUM(samples) FILTER (WHERE sum_delay_sec IS NOT NULL) > 10\n"
        "ORDER BY avg_min DESC NULLS LAST, route_code\n"
        f"LIMIT ${n}"
    )
    rows = await conn.fetch(sql, agency_id, *params, limit)
    return [(r["route_code"], None, r["dow"], r["avg_min"], r["samples"]) for r in rows]


# The service filter value naming each side of the weekday/weekend split.
_DAY_TYPE_SIDES: dict[str, ServiceType] = {"weekday": "平日", "weekend": "土日祝"}


async def _day_type_ctx(agency_id: int, ctx: RangeCtx, conn, service: ServiceType) -> RangeCtx:
    """``ctx``'s window, routes and time band over the dates ``service`` selects.

    The dates come from the agency's own calendar (:func:`api.range.resolve_service_dates`),
    so 土日祝 covers the public holidays and the other days the agency runs
    its weekend or holiday timetable, and 平日 leaves them out.
    """
    return await resolve_service_dates(
        RangeCtx(
            from_date=ctx.from_date,
            to_date=ctx.to_date,
            time_band=ctx.time_band,
            service=service,
            routes=ctx.routes,
        ),
        conn,
        agency_id,
    )


async def _dow_ranking_live(agency_id: int, overridden: RangeCtx, ch, label: str, limit: int) -> list[tuple]:
    """Live raw-scan dow-ranking — fallback for time_band-filtered queries."""
    cte_sql, ch_params = _dedup_cte_ch(overridden)
    result = await ch.query(
        f"WITH {cte_sql}\n"
        f"SELECT route_code, '{label}' AS dow,\n"
        "       avg(dep_delay) / 60.0 AS avg_min,\n"
        "       count(*) AS samples\n"
        "FROM deduped\n"
        "GROUP BY route_code\n"
        "HAVING count(*) > 10\n"
        "ORDER BY avg_min DESC, route_code\n"
        "LIMIT {dr_limit:UInt32}",
        parameters={"agency_id": agency_id, "dr_limit": limit, **ch_params},
    )
    # Round in Python (half-up) to match Postgres ROUND() — see _ranking_live.
    return [
        (route_code, None, dow, _round2(avg_min), samples) for route_code, dow, avg_min, samples in result.result_rows
    ]


@perf.timed("reports.compare_ranking")
@async_lru_cache(maxsize=64, ttl_seconds=300)
async def compute_compare_ranking(
    agency_id: int,
    ctx: RangeCtx,
    conn,
    limit: int | None = 100,
    ch=None,
) -> list[tuple]:
    """Per-route 平日-vs-土日祝 delay difference, sorted by absolute delta.

    The two sides are :func:`compute_dow_ranking`'s two day types, which
    replace the user's ``dow`` and ``service`` filters the same way, so each
    side's mean is the one that report gives the route. ``routes`` is kept so
    route-restricted comparisons work. ``limit=None`` returns every compared
    route, for a caller that reports the total.
    """
    if ctx.time_band != "all" and ch is None:
        raise RuntimeError("compute_compare_ranking's time_band-filtered live fallback requires a ClickHouse client")
    weekday_dates, weekend_dates = await _day_type_dates(agency_id, ctx, conn)
    if ctx.time_band != "all":
        return await _compare_ranking_live(agency_id, ctx, ch, weekday_dates, weekend_dates, limit)

    # Both sides from one agg_daily_trend pass (split by date via FILTER), summed
    # across service/date per route. dow and service stay 'all' so _agg_filter
    # emits neither clause.
    agg_ctx = RangeCtx(
        from_date=ctx.from_date,
        to_date=ctx.to_date,
        dow="all",
        time_band=ctx.time_band,
        service="all",
        routes=ctx.routes,
    )
    where, params, n = _agg_filter(agg_ctx, next_param=2)
    wd = f"date = ANY(${n}::date[])"
    we = f"date = ANY(${n + 1}::date[])"
    params.extend([weekday_dates, weekend_dates])
    n += 2
    # sum_delay_sec is nullable (unlike samples); each side's numerator AND
    # denominator (including the wd_n/we_n minimum-sample gate below) are
    # additionally FILTERed to sum_delay_sec IS NOT NULL so a row with
    # samples set but sum_delay_sec still NULL can't inflate wd_n/we_n past
    # the >10 gate while contributing nothing to wd_avg/we_avg — same
    # matched-population rationale as pipeline/reports/overview.py's
    # _route_weekly_history.
    wd_pop = f"{wd} AND sum_delay_sec IS NOT NULL"
    we_pop = f"{we} AND sum_delay_sec IS NOT NULL"
    sql = (
        "WITH per_route AS (\n"
        "    SELECT route_code,\n"
        f"           SUM(sum_delay_sec) FILTER (WHERE {wd_pop})::numeric\n"
        f"             / NULLIF(SUM(samples) FILTER (WHERE {wd_pop}), 0) / 60.0 AS wd_avg,\n"
        f"           SUM(samples) FILTER (WHERE {wd_pop}) AS wd_n,\n"
        f"           SUM(sum_delay_sec) FILTER (WHERE {we_pop})::numeric\n"
        f"             / NULLIF(SUM(samples) FILTER (WHERE {we_pop}), 0) / 60.0 AS we_avg,\n"
        f"           SUM(samples) FILTER (WHERE {we_pop}) AS we_n\n"
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
        "ORDER BY ABS(wd_avg - we_avg) DESC, route_code\n"
        f"LIMIT ${n}"
    )
    rows = await conn.fetch(sql, agency_id, *params, limit)
    return [tuple(r) for r in rows]


async def _day_type_dates(agency_id: int, ctx: RangeCtx, conn) -> tuple[list[date], list[date]]:
    """The dates of ``ctx``'s window the agency runs as 平日, and the rest (土日祝)."""
    weekday = selected_service_dates(await _day_type_ctx(agency_id, ctx, conn, "平日"))
    weekend = selected_service_dates(await _day_type_ctx(agency_id, ctx, conn, "土日祝"))
    return weekday or [], weekend or []


async def _route_wd_we_avg_ch(
    agency_id: int, ctx: RangeCtx, ch, weekday_dates: list[date], weekend_dates: list[date]
) -> dict[str, tuple[float, int, float, int]]:
    """Per-route (wd_avg_min, wd_n, we_avg_min, we_n) from ClickHouse `updates`,
    the sides being the service dates in ``weekday_dates`` and in
    ``weekend_dates`` (:func:`_day_type_dates`),
    computed with ONE query via conditional aggregates instead of two separate
    queries (weekday ctx + weekend ctx) plus a Python-side mean/count/HAVING
    reduction over every raw deduped row. The aggregation (and the >10-sample
    HAVING gate) runs in ClickHouse, so only one row per qualifying route
    crosses the wire rather than every raw deduped row.

    Deduped on a narrower key than `build_dedup_ch_sql` (no service_type/
    scheduled_time — the weekday-vs-weekend rollup doesn't need them; assumes
    (trip_id, date) determines service_type in clean data, same assumption
    the original Postgres wd_dedup/we_dedup CTEs made). Routes with <=10
    deduped weekday OR weekend observations are dropped entirely (both sides
    must clear the gate), mirroring the original SQL's HAVING COUNT(*) > 10
    on each side.

    The inner subquery's `dep_delay`/`captured_at` references are qualified
    with the `u.` table alias for the same reason `build_dedup_ch_sql`
    qualifies them (see its docstring): the inner SELECT list defines
    `dep_delay` as an `argMax(...)` alias, and ClickHouse would otherwise
    textually substitute a bare `dep_delay` in that same subquery's WHERE
    with the aggregate expression, raising `ILLEGAL_AGGREGATION`.
    """
    combined_ctx = RangeCtx(
        from_date=ctx.from_date,
        to_date=ctx.to_date,
        dow="all",  # both sides needed in one pass; split by service date below
        time_band=ctx.time_band,
        service="all",
        routes=ctx.routes,
    )
    where_frag, params = build_updates_filter_ch(combined_ctx)
    service_date = service_date_ch("u.captured_at")
    result = await ch.query(
        f"""
        SELECT route_code,
               avgIf(dep_delay, sd IN {{cmp_wd:Array(Date)}}) AS wd_avg,
               countIf(sd IN {{cmp_wd:Array(Date)}})          AS wd_n,
               avgIf(dep_delay, sd IN {{cmp_we:Array(Date)}}) AS we_avg,
               countIf(sd IN {{cmp_we:Array(Date)}})          AS we_n
        FROM (
            SELECT u.route_code AS route_code,
                   argMax(u.dep_delay, (u.captured_at, u.file_name)) AS dep_delay,
                   {service_date} AS sd
            FROM updates AS u
            WHERE u.agency_id = {{agency_id:UInt16}} AND u.dep_delay IS NOT NULL AND {where_frag}
            GROUP BY u.route_code, u.trip_id, {service_date}, u.stop_sequence
        )
        GROUP BY route_code
        HAVING wd_n > 10 AND we_n > 10
        """,
        parameters={"agency_id": agency_id, "cmp_wd": weekday_dates, "cmp_we": weekend_dates, **params},
    )
    return {
        route_code: (wd_avg / 60.0, wd_n, we_avg / 60.0, we_n)
        for route_code, wd_avg, wd_n, we_avg, we_n in result.result_rows
    }


async def _compare_ranking_live(
    agency_id: int, ctx: RangeCtx, ch, weekday_dates: list[date], weekend_dates: list[date], limit: int | None
) -> list[tuple]:
    """Live raw-scan 平日-vs-土日祝 compare — fallback for time_band queries."""
    stats = await _route_wd_we_avg_ch(agency_id, ctx, ch, weekday_dates, weekend_dates)

    out: list[tuple] = []
    for route_code, (wd_mean, _wd_n, we_mean, _we_n) in stats.items():
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
    # ABS(wd.avg_min - we.avg_min), computed before the display-only ROUND),
    # route_code as an explicit tie-break: `stats` is built from ClickHouse's
    # arbitrary GROUP BY output order, so two routes tied on delta would
    # otherwise keep whatever order they happened to arrive in — the same
    # bug class already fixed for movers ranking in overview.py.
    out.sort(key=lambda r: (-abs(stats[r[0]][0] - stats[r[0]][2]), r[0]))
    return out[:limit]


# Trend (new endpoint, not just a report row)


@perf.timed("reports.hourly_heatmap")
@async_lru_cache(maxsize=16, ttl_seconds=300)
async def compute_hourly_heatmap(
    agency_id: int,
    ctx: RangeCtx,
    conn,
    ch=None,
) -> list[dict]:
    """Hour-of-day × date cells for the granular trend view.

    Returns ``[ { date, hour, avg_min, samples, sum_delay_sec } ]`` filtered
    by the same ctx the rest of the trend uses. Hour is extracted from
    ``scheduled_time`` (a TIME column post migration 0011); cells with too
    few samples (<3) are dropped to keep the rendering signal-strong.

    ``sum_delay_sec`` is each cell's exact raw-seconds delay total — on the
    fast path, nullable until the next ``make analyze-all`` rewrites the row
    (migration 0030 added the column without a backfill, same rationale as
    migration 0028). A caller that only reads one cell at a time (e.g.
    rendering the raw per-hour heatmap) has no use for it and can ignore it;
    a caller that pools MULTIPLE cells together (e.g.
    ``pipeline.reports.forecast.hourly_cells_to_dow_band``, which pools
    across dates into a dow×band grid) needs it to compute the true pooled
    mean instead of re-weighting this function's own already-rounded
    ``avg_min`` (each cell's ``avg_min`` here is itself a single rounding —
    fine standalone, but re-weighting several already-rounded cells is a
    second, compounding rounding — see migration 0030's own rationale for
    the general defect class).
    """
    # agg_hour_daily is already (date, hour, avg_min, samples) across all routes —
    # a direct read. It has no route/hour-band dimension, so either filter
    # falls back to the live dedup scan (ClickHouse). DOW and the service
    # filter (both on the date column) are fine.
    if ctx.time_band == "all" and not ctx.routes:
        where, params, _ = _dist_filter(ctx, next_param=2)
        sql = (
            "SELECT date, hour, avg_min, samples, sum_delay_sec\n"
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
        if ch is None:
            raise RuntimeError("compute_hourly_heatmap's live fallback requires a ClickHouse client")
        cte_sql, ch_params = _dedup_cte_ch(ctx)
        # OrNull, not a bare cast: ingest now zero-pads every scheduled_time's
        # hour (pipeline/strategies/static_join.py), but this degrades any
        # already-ingested pre-fix row (or any future malformed value) out of
        # the histogram instead of raising CANNOT_PARSE_TEXT and 500ing the
        # whole /reports/trend heatmap over a handful of bad rows.
        hour_expr = "toUInt8OrNull(substring(scheduled_time, 1, 2))"
        result = await ch.query(
            f"WITH {cte_sql}\n"
            f"SELECT date, {hour_expr} AS hour,\n"
            "       avg(dep_delay) / 60.0 AS avg_min,\n"
            "       sum(dep_delay) AS sum_delay_sec,\n"
            "       count(*) AS samples\n"
            "FROM deduped\n"
            "WHERE scheduled_time IS NOT NULL\n"
            f"GROUP BY date, {hour_expr}\n"
            "HAVING count(*) >= 3 AND hour IS NOT NULL\n"
            "ORDER BY date, hour",
            parameters={"agency_id": agency_id, **ch_params},
        )
        rows = _ch_rows(result)
    # Round in Python (half-up) to match Postgres ROUND() — see _ranking_live.
    # Idempotent on the fast path, whose agg_hour_daily.avg_min is already
    # stored rounded to 2 dp. sum_delay_sec is passed through unrounded (it's
    # already an exact integer on both paths) — see this function's own
    # docstring for why a pooling caller needs it.
    return [
        {
            "date": r["date"].isoformat(),
            "hour": int(r["hour"]),
            "avg_min": float(_round2(r["avg_min"])) if r["avg_min"] is not None else None,
            "samples": r["samples"],
            "sum_delay_sec": int(r["sum_delay_sec"]) if r["sum_delay_sec"] is not None else None,
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
    ``{ days: [{ date, avg_min, samples, avg_min_smoothed, top_offenders: [...] }] }``
    where ``date`` is the bucket start date (ISO string). ``avg_min_smoothed``
    is a trailing sample-weighted mean over the last (up to) ``_SMOOTH_WINDOW``
    OBSERVED buckets ending at that one (position-based, not calendar-day-
    anchored — a bucket with no service that day simply isn't in the window,
    same "observed days only" convention as
    ``pipeline.reports.forecast.summarize_agency_overview``'s ``recent_daily``)
    — a low-traffic route/day's raw figure is visually noisy day to day, and
    this sits ALONGSIDE it (not a replacement) so the UI can show both.
    Only computed for ``granularity == 'day'``; a week/month bucket is
    already smoothed by its own wider width, so ``avg_min_smoothed`` is
    ``None`` there.
    """
    _SMOOTH_WINDOW = 7
    # A route needs more samples than this in a bucket to be named an offender.
    _OFFENDER_MIN_SAMPLES = 5
    # Map granularity to a date_trunc unit; fall back to 'day' for unknown values.
    _TRUNC = {"day": "day", "week": "week", "month": "month"}
    trunc_unit = _TRUNC.get(granularity, "day")

    if ctx.time_band == "all":
        # Sum agg_daily_trend into one row per (bucket, route), across the raw
        # service ids a route's observations matched (see _service_label).
        # A week/month bucket pools MULTIPLE agg_daily_trend rows, so this reads
        # each row's exact sum_delay_sec (not its rounded avg_min) and divides
        # once at the end — see analyze.py's module docstring for why pooling
        # already-rounded per-bucket means would otherwise drift from the true
        # pooled mean. sum_delay_sec is nullable (unlike samples), so a week/
        # month bucket spanning a date whose row analyze() hasn't rewritten
        # since migration 0028 could otherwise mix a populated date with a
        # NULL one; FILTER both sides to the same row population — see
        # pipeline/reports/overview.py's _route_weekly_history for the
        # identical rationale (this SQL-level pooling is a separate, coarser
        # grain from this function's own Python-side by_date_samples/
        # by_date_weighted_sec pooling further below, which already applies
        # the same guard one level up, across routes within a bucket rather
        # than across dates within one route).
        where, params, _ = _agg_filter(ctx, next_param=2)
        sql = (
            f"SELECT date_trunc('{trunc_unit}', date::timestamp)::date AS bucket,\n"
            "       route_code,\n"
            "       SUM(sum_delay_sec) FILTER (WHERE sum_delay_sec IS NOT NULL)::bigint AS sum_delay_sec,\n"
            "       SUM(samples) FILTER (WHERE sum_delay_sec IS NOT NULL)::int AS samples\n"
            "FROM agg_daily_trend\n"
            f"WHERE agency_id = $1 AND {where}\n"
            "GROUP BY bucket, route_code"
        )
        per_day = await conn.fetch(sql, agency_id, *params)
    else:
        # time_band filter needs the hour-of-day, only on raw updates
        # (ClickHouse). Every group has at least one row, so avg() is never NaN.
        if ch is None:
            raise RuntimeError("compute_trend_series's time_band-filtered live fallback requires a ClickHouse client")
        _CH_BUCKET_EXPR = {"day": "date", "week": "toStartOfWeek(date, 1)", "month": "toStartOfMonth(date)"}
        bucket_expr = _CH_BUCKET_EXPR.get(granularity, "date")
        cte_sql, ch_params = _dedup_cte_ch(ctx)
        result = await ch.query(
            f"WITH {cte_sql}\n"
            f"SELECT {bucket_expr} AS bucket,\n"
            "       route_code,\n"
            "       sum(dep_delay) AS sum_delay_sec,\n"
            "       count(*) AS samples\n"
            "FROM deduped\n"
            "GROUP BY bucket, route_code",
            parameters={"agency_id": agency_id, **ch_params},
        )
        per_day = _ch_rows(result)

    by_date_samples: dict = {}
    by_date_weighted_sec: dict = {}
    by_date: dict = {}
    for r in per_day:
        # The SQL aliases the time-bucketed column as "bucket".
        d = r["bucket"]
        n = r["samples"]
        sum_sec = r["sum_delay_sec"]
        # Per-(bucket, route) display average, rounded once straight
        # from the exact raw-seconds sum (half-up, matching Postgres ROUND()).
        avg = float(_round2(sum_sec / n / 60.0)) if n and sum_sec is not None else None
        # sum_delay_sec is nullable (unlike samples); only add a group's n to
        # by_date_samples alongside its sum_sec to by_date_weighted_sec — both
        # dicts must describe the same row population, or the bucket-level
        # avg_min computed from them below would be silently biased down by
        # a group whose samples counted here but whose delay total didn't
        # (matches pipeline/reports/overview.py's identical FILTER rationale
        # applied at the SQL layer for other queries).
        if sum_sec is None:
            continue
        by_date_samples[d] = by_date_samples.get(d, 0) + n
        by_date_weighted_sec[d] = by_date_weighted_sec.get(d, 0) + sum_sec
        # Every group counts toward its bucket's average and sample total, as
        # in Overview's pooling of agg_daily_trend. Only the worst-route
        # attribution applies a floor: a handful of trips is too noisy to
        # name a worst route.
        by_date.setdefault(d, [])
        if n > _OFFENDER_MIN_SAMPLES:
            by_date[d].append(
                {
                    "route_code": r["route_code"],
                    "avg_min": avg,
                    "samples": n,
                }
            )

    daily = []
    for d in sorted(by_date.keys()):
        # by_date and by_date_samples share one population guard: a bucket
        # where every route's sum_delay_sec is NULL has an entry in neither,
        # so it is absent from the series rather than a zero row.
        n = by_date_samples.get(d, 0)
        # Whole-bucket total pooled from every route's exact
        # raw-seconds sum, dividing once at the end — NOT from re-weighting
        # each group's already-rounded display avg_min above.
        avg = round((by_date_weighted_sec.get(d, 0) / n) / 60, 2) if n else None
        daily.append({"date": d, "avg_min": avg, "samples": n})

    days = []
    for i, r in enumerate(daily):
        # None (no data) sorts last; among real values, worst delay first.
        # Two-pass stable sort: pre-sort by route_code so ties on avg_min
        # break deterministically in ascending route_code order — `per_day`
        # comes from a GROUP BY with no ordering guarantee, so leaving ties
        # unresolved would make the top-offenders cut unstable run to run.
        offenders = sorted(
            sorted(by_date.get(r["date"], []), key=lambda x: x["route_code"]),
            key=lambda x: (x["avg_min"] is None, -(x["avg_min"] or 0)),
        )[:top_offenders]
        # Trailing window over the last _SMOOTH_WINDOW OBSERVED buckets
        # (position-based via `daily`'s sorted index), pooled from the same
        # exact raw-seconds sums as the per-bucket avg_min above — dividing
        # once at the end, not re-weighting already-rounded per-bucket means.
        avg_min_smoothed = None
        # trunc_unit (not the raw `granularity` arg) is the actual bucket
        # width used above on BOTH the fast (`_TRUNC.get`) and live
        # (`_CH_BUCKET_EXPR.get`) paths, including their identical
        # unrecognized-value fallback to a per-day bucket.
        if trunc_unit == "day":
            window_dates = [daily[j]["date"] for j in range(max(0, i - _SMOOTH_WINDOW + 1), i + 1)]
            window_samples = sum(by_date_samples.get(d, 0) for d in window_dates)
            if window_samples:
                window_sec = sum(by_date_weighted_sec.get(d, 0) for d in window_dates)
                avg_min_smoothed = round((window_sec / window_samples) / 60, 2)
        days.append(
            {
                "date": r["date"].isoformat(),
                "avg_min": r["avg_min"],
                "avg_min_smoothed": avg_min_smoothed,
                "samples": r["samples"],
                "top_offenders": offenders,
            }
        )
    return {"days": days}
