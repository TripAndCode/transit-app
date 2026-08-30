"""SQL helpers backing the LLM tool surface (pipeline/query/tools.py).

Each function is small, focused, and returns plain rows / tuples that
tools.py wraps in a ToolResult. None of these are exposed via REST —
the legacy /api/{id}/query route was retired with executor.py. SQL is
lifted from the corresponding _exec_* functions, with one intentional
change: ``route_dow_breakdown`` always reads from the deduped
``updates`` table and honours ``ctx`` (date / DOW / time band /
service), where the old executor's by_dow path silently preferred the
all-time ``agg_route_dow`` aggregate when present, ignoring the
requested window.

``route_dow_breakdown`` / ``route_compare_service`` always read the live
``updates`` table (there is no agg-table fast path for either), which now
lives in ClickHouse — both take a ``ch`` client. ``conn`` (asyncpg) is kept
in the signature for call-site stability even though these two no longer
use it for their own query. ``ch`` defaults to ``None`` (returning an empty
result rather than raising) for callers/tests that exercise routing logic
(alias resolution, unsupported-tool handling, ...) without a real
ClickHouse client attached.

That ``ch is None`` fallback is presently unreachable from real HTTP
dispatch: ``api.deps.get_ch`` never hands out a bare ``None`` — when
ClickHouse didn't come up at startup it returns a ``_ClickHouseUnavailable``
stand-in that raises ``HTTPException(503)`` on first use instead (see
api/deps.py). So a real request either gets a working client or 503s before
ever reaching this ``is None`` check; the guard is kept only for direct
unit-test callers that construct these functions' args by hand without
wiring a client at all.
"""

from dataclasses import replace

from api.range import RangeCtx
from pipeline.reports.filters import _dedup_cte_ch
from pipeline.reports.rankings import _round2, compute_trend_series


async def route_dow_breakdown(
    agency_id: int,
    ctx: RangeCtx,
    conn,
    ch=None,
    *,
    route: str,
) -> list[tuple]:
    """Per-DOW delay summary for one route over ctx.

    Returns rows: (route_code, service_type, dow_iso, avg_min, samples)
    sorted by avg_min DESC. dow_iso is ISODOW (Mon=1..Sun=7) —
    ``toDayOfWeek``'s default mode matches Postgres's ``EXTRACT(ISODOW ...)``
    numbering (see api.range.dow_clause_ch's docstring). Backs
    tools._tool_route_stats. Every group has >= 1 row (no HAVING gate here,
    matching the original query), so avg() is never NaN.
    """
    if ch is None:
        return []
    cte_sql, ch_params = _dedup_cte_ch(ctx)
    result = await ch.query(
        f"WITH {cte_sql}\n"
        "SELECT route_code, service_type,\n"
        "       toDayOfWeek(date) AS dow,\n"
        "       avg(dep_delay) / 60.0 AS avg_min,\n"
        "       count(*) AS samples\n"
        "FROM deduped\n"
        "WHERE route_code = {rdb_route:String}\n"
        "GROUP BY route_code, service_type, toDayOfWeek(date)\n"
        "ORDER BY avg_min DESC",
        parameters={"agency_id": agency_id, "rdb_route": str(route), **ch_params},
    )
    # ClickHouse's round() is round-half-to-even; round in Python (half-up) to
    # match Postgres ROUND() and this codebase's other ClickHouse live-path
    # roundings — see pipeline.reports.rankings._ranking_live.
    return [
        (route_code, service_type, dow, _round2(avg_min), samples)
        for route_code, service_type, dow, avg_min, samples in result.result_rows
    ]


async def route_compare_service(
    agency_id: int,
    ctx: RangeCtx,
    conn,
    ch=None,
    *,
    route: str,
) -> list[tuple]:
    """Single-route weekday vs weekend delay comparison over ctx.

    Returns rows: (service_type, avg_min, samples) — typically two rows
    ('平日' and '土日祝'). Backs tools._tool_compare_segments
    (dimension='service_type'). Every group has >= 1 row, so avg() is
    never NaN.
    """
    if ch is None:
        return []
    cte_sql, ch_params = _dedup_cte_ch(ctx)
    result = await ch.query(
        f"WITH {cte_sql}\n"
        "SELECT service_type,\n"
        "       avg(dep_delay) / 60.0 AS avg_min,\n"
        "       count(*) AS samples\n"
        "FROM deduped\n"
        "WHERE route_code = {rcs_route:String}\n"
        "GROUP BY service_type",
        parameters={"agency_id": agency_id, "rcs_route": str(route), **ch_params},
    )
    # Round in Python (half-up) to match Postgres ROUND() — see
    # route_dow_breakdown / pipeline.reports.rankings._ranking_live.
    return [(service_type, _round2(avg_min), samples) for service_type, avg_min, samples in result.result_rows]


async def route_info(agency_id: int, conn, *, route: str) -> tuple | None:
    """Static route metadata: route_id, route_short_name, stop count,
    first/last departure, trip count. Returns None when the route isn't
    in the agency's static GTFS at all. Backs tools._tool_route_meta.
    """
    row = await conn.fetchrow(
        "SELECT sr.route_id, sr.route_short_name, "
        "       COUNT(DISTINCT sst.stop_id) AS stop_count, "
        "       MIN(sst.departure_time) AS first_dep, "
        "       MAX(sst.departure_time) AS last_dep, "
        "       COUNT(DISTINCT st.trip_id) AS trip_count "
        "FROM static_routes sr "
        "JOIN static_trips st ON st.route_id = sr.route_id AND st.agency_id=$1 "
        "JOIN static_stop_times sst ON sst.trip_id = st.trip_id AND sst.agency_id=$1 "
        "WHERE sr.agency_id=$1 AND sr.route_id LIKE '%(' || $2 || ')' "
        "GROUP BY sr.route_id, sr.route_short_name",
        agency_id,
        str(route),
    )
    return tuple(row) if row else None


async def segment_hotspots(
    agency_id: int,
    ctx: RangeCtx,
    conn,
    ch=None,
    *,
    route: str,
    limit: int = 5,
) -> list[tuple]:
    """Worst stop_sequences by average delay for one route over ctx.

    Returns rows: (stop_sequence, stop_name, avg_min, samples), sorted by
    avg_min DESC, limited to `limit`. Only stop_sequences with > 5 samples
    are returned (matches agg_stop_seq's own noise gate in
    pipeline/analyze.py). Backs tools._tool_segment_hotspots.

    `updates` lives in ClickHouse; static_stop_times/static_stops live in
    Postgres — no cross-database join, so this runs in two steps: (1) a
    ctx-bounded ClickHouse aggregate grouped by stop_sequence, picking one
    representative trip_id per group via any() to resolve a stop name from,
    (2) a Postgres lookup for those (trip_id, stop_sequence) pairs.
    """
    if ch is None:
        return []
    cte_sql, ch_params = _dedup_cte_ch(ctx)
    result = await ch.query(
        f"WITH {cte_sql}\n"
        "SELECT stop_sequence, any(trip_id) AS sample_trip_id,\n"
        "       avg(dep_delay) / 60.0 AS avg_min,\n"
        "       count(*) AS samples\n"
        "FROM deduped\n"
        "WHERE route_code = {sh_route:String} AND stop_sequence IS NOT NULL\n"
        "GROUP BY stop_sequence\n"
        "HAVING count(*) > 5\n"
        "ORDER BY avg_min DESC\n"
        "LIMIT {sh_limit:UInt32}",
        parameters={"agency_id": agency_id, "sh_route": str(route), "sh_limit": limit, **ch_params},
    )
    ch_rows = result.result_rows
    if not ch_rows:
        return []
    trip_ids = [r[1] for r in ch_rows]
    stop_sequences = [r[0] for r in ch_rows]
    name_rows = await conn.fetch(
        "SELECT u.stop_sequence, COALESCE(MAX(ss.stop_name), u.stop_sequence::text || '番停留所') AS stop_name "
        "FROM UNNEST($2::text[], $3::int[]) AS u(trip_id, stop_sequence) "
        "LEFT JOIN static_stop_times sst "
        "  ON sst.trip_id = u.trip_id AND sst.stop_sequence = u.stop_sequence AND sst.agency_id = $1 "
        "LEFT JOIN static_stops ss ON ss.stop_id = sst.stop_id AND ss.agency_id = $1 "
        "GROUP BY u.stop_sequence",
        agency_id,
        trip_ids,
        stop_sequences,
    )
    name_by_seq = {r["stop_sequence"]: r["stop_name"] for r in name_rows}
    return [
        (stop_sequence, name_by_seq.get(stop_sequence, f"{stop_sequence}番停留所"), _round2(avg_min), samples)
        for stop_sequence, _trip_id, avg_min, samples in ch_rows
    ]


async def route_hour_dow_pattern(
    agency_id: int,
    conn,
    *,
    route: str,
    top_n: int = 3,
) -> list[tuple]:
    """Worst hour x day-of-week combinations for one route, pooled across
    service types (sample-weighted mean, same formula as
    api/routers/reports.py's expected-delay-heatmap endpoint).

    agg_route_hour_dow has no date column — this is an all-time seasonal
    pattern, not scoped to any ctx date range (by design, matches how the
    existing Forecast heatmap endpoints already read this table). Backs
    tools._tool_time_pattern.

    Returns rows: (dow, hour, avg_min, samples), sorted by avg_min DESC,
    limited to top_n.
    """
    rows = await conn.fetch(
        "SELECT dow, hour, "
        "SUM(avg_min * samples) / NULLIF(SUM(samples), 0) AS avg_min, "
        "SUM(samples)::int AS samples "
        "FROM agg_route_hour_dow "
        "WHERE agency_id = $1 AND route_code = $2 AND avg_min IS NOT NULL AND samples > 0 "
        "GROUP BY dow, hour "
        "HAVING SUM(samples) > 5 "
        "ORDER BY avg_min DESC "
        "LIMIT $3",
        agency_id,
        str(route),
        top_n,
    )
    return [(r["dow"], r["hour"], _round2(r["avg_min"]), r["samples"]) for r in rows]


async def schedule_realism_segments(
    agency_id: int,
    ctx: RangeCtx,
    conn,
    ch=None,
    *,
    route: str,
    limit: int = 5,
) -> list[tuple]:
    """Stop-to-stop segments where delay is systematically ADDED, not just
    present. For each trip, computes the change in dep_delay between each
    pair of consecutive observed stop_sequences (using ClickHouse's
    leadInFrame window function), then averages that change per
    (stop_sequence, next_stop_sequence) pair across all trips in ctx.

    A large positive average means the timetable's padding for that
    specific gap is systematically too tight (delay grows crossing it on
    most trips, not just outliers) — distinct from segment_hotspots, which
    ranks absolute delay level and can't tell "high because it started high
    upstream" apart from "high because this segment itself is the
    bottleneck". Backs tools._tool_schedule_realism.

    `trip_id` in this feed is a recurring GTFS *schedule* identifier (e.g.
    "平日_8時15分_系統3" — see pipeline/strategies/aomori_regex.py), not a
    per-day run identifier: the same trip_id recurs on every day that
    service pattern operates. Partitioning the window function by
    `trip_id` alone would throw every day's rows for a recurring trip_id
    into one partition ordered only by `stop_sequence` — same-stop_sequence
    rows from different days become adjacent ties in unspecified order, so
    `leadInFrame` can pair one day's stop k against another day's stop k+1,
    silently corrupting avg_added_min and undercounting samples. Partition
    by `(trip_id, date)` instead — `date` is already selected by `deduped`
    (see pipeline/db.py's build_dedup_ch_sql) — so each calendar day's run
    of a recurring trip_id is windowed independently.

    Returns rows: (stop_sequence, next_stop_sequence, avg_added_min, samples),
    sorted by avg_added_min DESC, samples > 5 only, limited to `limit`.
    """
    if ch is None:
        return []
    cte_sql, ch_params = _dedup_cte_ch(ctx)
    result = await ch.query(
        f"WITH {cte_sql},\n"
        "     with_next AS (\n"
        "         SELECT trip_id, date, stop_sequence, dep_delay,\n"
        "                leadInFrame(dep_delay) OVER (\n"
        "                    PARTITION BY trip_id, date ORDER BY stop_sequence\n"
        "                    ROWS BETWEEN CURRENT ROW AND 1 FOLLOWING\n"
        "                ) AS next_dep_delay,\n"
        "                leadInFrame(stop_sequence) OVER (\n"
        "                    PARTITION BY trip_id, date ORDER BY stop_sequence\n"
        "                    ROWS BETWEEN CURRENT ROW AND 1 FOLLOWING\n"
        "                ) AS next_stop_sequence\n"
        "         FROM deduped\n"
        "         WHERE route_code = {sr_route:String} AND stop_sequence IS NOT NULL\n"
        "     )\n"
        "SELECT stop_sequence, next_stop_sequence,\n"
        "       avg(next_dep_delay - dep_delay) / 60.0 AS avg_added_min,\n"
        "       count(*) AS samples\n"
        "FROM with_next\n"
        "WHERE next_stop_sequence = stop_sequence + 1\n"
        "GROUP BY stop_sequence, next_stop_sequence\n"
        "HAVING count(*) > 5\n"
        "ORDER BY avg_added_min DESC\n"
        "LIMIT {sr_limit:UInt32}",
        parameters={"agency_id": agency_id, "sr_route": str(route), "sr_limit": limit, **ch_params},
    )
    return [
        (stop_sequence, next_stop_sequence, _round2(avg_added_min), samples)
        for stop_sequence, next_stop_sequence, avg_added_min, samples in result.result_rows
    ]


async def route_trend_shift(
    agency_id: int,
    ctx: RangeCtx,
    conn,
    ch=None,
    *,
    route: str,
) -> dict | None:
    """Chronic pattern vs regime-shift check for one route over ctx.

    Reuses compute_trend_series (the same helper backing the Trend chart),
    scoped to the one route via ctx.routes, then splits the returned
    per-day series into first-half / second-half and compares their
    sample-weighted means. A large delta_min means something changed partway
    through the window (regime shift); a small delta_min means the pattern
    has been consistent throughout (chronic). Backs tools._tool_trend_shift.

    Returns None when there are fewer than two daily buckets for the route in
    ctx — a single day has no first/second half to compare, so a mechanical
    0.00 delta would misreport "not enough data" as "stable, no change."
    """
    route_ctx = replace(ctx, routes=(str(route),))
    series = await compute_trend_series(agency_id, route_ctx, conn, ch=ch)
    days = [d for d in series.get("days", []) if d.get("samples")]
    if len(days) < 2:
        return None
    midpoint = len(days) // 2
    first_half, second_half = days[:midpoint], days[midpoint:]

    def _weighted_mean(bucket: list[dict]) -> float:
        total_samples = sum(d["samples"] for d in bucket)
        if total_samples == 0:
            return 0.0
        return sum(d["avg_min"] * d["samples"] for d in bucket) / total_samples

    first_avg = _weighted_mean(first_half)
    second_avg = _weighted_mean(second_half)
    # float(...) wrap matches the established convention for dict-shaped
    # (as opposed to row-tuple) return values elsewhere in this codebase —
    # see compute_trend_series / pipeline.reports.overview — since _round2
    # alone returns a Decimal, which pytest.approx (and plain arithmetic)
    # can't safely mix with float.
    return {
        "first_half_avg_min": float(_round2(first_avg)),
        "second_half_avg_min": float(_round2(second_avg)),
        "delta_min": float(_round2(second_avg - first_avg)),
        "days": len(days),
    }
