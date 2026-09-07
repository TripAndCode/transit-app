"""Monthly/annual report template for a local public-transport council
audience, plus a per-trip "delay certificate" export.

``compute_council_summary`` pools item 90's on-time/late tolerance (resolved
by :mod:`pipeline.reports.definition`, item 91) and item 92's
service-delivered rate into ONE whole-agency figure for ``ctx``'s range --
unlike :func:`pipeline.reports.rankings.compute_on_time`, which reports
per-route, and unlike :func:`pipeline.reports.network.compute_network_summary`,
whose on-time figure is always the ``legacy_60s`` exact column with no
tolerance override. The fast path reuses
``pipeline.reports.rankings``'s own ``_read_dist_scalars``/
``_read_dist_with_hist`` SQL byte-for-byte, simply pooling every returned
route/service group into one total instead of emitting one row per group
(with no per-group minimum-sample gate -- that gate exists in
``compute_on_time`` to keep a *ranking* free of thin-sample noise, which has
no meaning for a single pooled total). Service-delivered numbers come
straight from :func:`pipeline.reports.service_delivered.compute_service_delivered_by_agency`
(item 92) -- this module never recomputes that ratio a second way.

``compute_delay_certificate`` lists every individual departure observation in
``ctx``'s range whose ``dep_delay`` STRICTLY EXCEEDS a configurable
threshold -- a self-contained per-trip record (the agency's name is embedded
in every row, unlike every other report in this package, which is already
scoped to one agency by its request URL) suitable for handing to a third
party outside this app's UI. Always a live ClickHouse scan: no precomputed
aggregate stores individual trip-level rows, so -- unlike every other report
here, which only falls back to a live scan under a ``time_band`` filter --
there is no fast path at all, only
:func:`pipeline.reports.filters._dedup_cte_ch`'s shared dedup+ctx-filter CTE.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from api.clickhouse import max_captured_at_before
from api.range import RangeCtx
from pipeline.freshness import is_stale
from pipeline.histogram import (
    LEGACY_ON_TIME_LATE_TOLERANCE_SEC,
    LEGACY_SEVERE_LATE_TOLERANCE_SEC,
    N_BUCKETS,
    count_in_range,
)
from pipeline.reports.filters import _dedup_cte_ch
from pipeline.reports.rankings import _avg_min, _read_dist_scalars, _read_dist_with_hist, _round1
from pipeline.reports.service_delivered import compute_service_delivered_by_agency

_log = logging.getLogger(__name__)

_JST = ZoneInfo("Asia/Tokyo")

# The delay-certificate export's default "exceeds" threshold, in seconds.
# Reuses the existing severe-late constant (item 90) rather than a new
# independent literal -- it is not tied to that preset's own meaning (a
# caller can override via the endpoint's `threshold_sec` query param), just a
# sensible, already-established default magnitude.
DEFAULT_DELAY_CERTIFICATE_THRESHOLD_SEC = LEGACY_SEVERE_LATE_TOLERANCE_SEC


async def _agency_on_time_pooled(
    agency_id: int,
    ctx: RangeCtx,
    conn,
    ch,
    early_tolerance_sec: int | None,
    late_tolerance_sec: int | None,
) -> tuple[float | None, float | None, int]:
    """``(on_time_pct, avg_delay_min, samples)`` pooled across EVERY
    route/service group matching ``ctx`` -- the same tolerance semantics
    :func:`pipeline.reports.rankings.compute_on_time` resolves per-route,
    collapsed to one agency-wide figure. ``(None, None, 0)`` when nothing
    matches ``ctx`` at all.
    """
    low_sec = None if early_tolerance_sec is None else -early_tolerance_sec
    high_sec = LEGACY_ON_TIME_LATE_TOLERANCE_SEC if late_tolerance_sec is None else late_tolerance_sec
    is_legacy = early_tolerance_sec is None and late_tolerance_sec is None

    if ctx.time_band != "all":
        if ch is None:
            raise RuntimeError("council summary's time_band-filtered live fallback requires a ClickHouse client")
        cte_sql, ch_params = _dedup_cte_ch(ctx)
        low_clause = "1=1" if low_sec is None else "dep_delay >= {ot_low:Int32}"
        result = await ch.query(
            f"WITH {cte_sql}\n"
            "SELECT\n"
            f"    sum(CASE WHEN dep_delay <= {{ot_high:Int32}} AND {low_clause} THEN 1.0 ELSE 0 END) AS on_time_n,\n"
            "    sum(dep_delay) AS sum_delay_sec,\n"
            "    count(*) AS samples\n"
            "FROM deduped",
            parameters={
                "agency_id": agency_id,
                "ot_high": high_sec,
                "ot_low": 0 if low_sec is None else low_sec,
                **ch_params,
            },
        )
        on_time_n, sum_delay_sec, samples = result.result_rows[0]
        if not samples:
            return None, None, 0
        return (
            float(_round1(on_time_n * 100.0 / samples)),
            float(_avg_min(sum_delay_sec, samples)),
            int(samples),
        )

    if is_legacy:
        rows = await _read_dist_scalars(agency_id, ctx, conn)
        samples = sum(r["samples"] for r in rows)
        if not samples:
            return None, None, 0
        on_time_count = sum(r["on_time_count"] for r in rows)
        # Postgres SUM(bigint) returns NUMERIC (asyncpg -> Decimal); _avg_min
        # wraps it in Decimal(...) itself (safe whether the input here is
        # already Decimal or a plain int) rather than dividing by the float
        # 60.0 directly, which raises TypeError mixing Decimal and float.
        sum_delay_sec = sum(r["sum_delay_sec"] for r in rows)
        return (
            float(_round1(on_time_count * 100.0 / samples)),
            float(_avg_min(sum_delay_sec, samples)),
            samples,
        )

    rows = await _read_dist_with_hist(agency_id, ctx, conn)
    samples = sum(r["samples"] for r in rows)
    if not samples:
        return None, None, 0
    sum_delay_sec = sum(r["sum_delay_sec"] for r in rows)
    merged_hist = [0] * N_BUCKETS
    for r in rows:
        for i, c in enumerate(r["hist"]):
            merged_hist[i] += c
    on_time_est = count_in_range(merged_hist, low_sec, high_sec)
    return (
        float(_round1(on_time_est * 100.0 / samples)),
        float(_avg_min(sum_delay_sec, samples)),
        samples,
    )


async def compute_council_summary(
    agency_id: int,
    ctx: RangeCtx,
    conn,
    ch,
    early_tolerance_sec: int | None = None,
    late_tolerance_sec: int | None = None,
) -> dict:
    """Whole-agency monthly/annual figures for ``ctx``'s range: pooled
    on-time rate + item 92's service-delivered rate + the freshness/quality
    signals a council audience needs before trusting either, all in ONE
    dict. This function only computes numbers -- footnote/headline TEXT is
    rendered separately by
    :func:`pipeline.query.formatter.format_council_summary_text` from this
    dict plus the caller's own :class:`~pipeline.reports.definition.DefinitionMeta`
    (:func:`~pipeline.reports.definition.resolve_definition_meta`).

    Returns ``{"on_time_pct", "avg_delay_min", "samples", "planned_trips",
    "executed_trips", "service_delivered_pct", "is_stale", "clamp_pct"}``.
    ``executed_trips``/``service_delivered_pct`` are ``None`` together
    exactly when :func:`~pipeline.reports.service_delivered.compute_service_delivered_by_agency`
    says so (see that module). ``clamp_pct`` is ``None`` when this agency has
    no ``agg_feed_health`` rows in range (raw_samples == 0) -- "not
    measurable", not a misleading 0%.
    """
    on_time_pct, avg_delay_min, samples = await _agency_on_time_pooled(
        agency_id, ctx, conn, ch, early_tolerance_sec, late_tolerance_sec
    )

    delivered = await compute_service_delivered_by_agency(conn, [agency_id], ctx.from_date, ctx.to_date)
    d = delivered.get(agency_id, {"planned_trips": 0, "executed_trips": None, "service_delivered_pct": None})

    # Freshness (agg-lag) + quality (implausible-reading share), scoped to
    # THIS agency's ctx.from_date/to_date range -- the same two signals
    # pipeline.reports.network.compute_network_summary already exposes
    # network-wide, computed here for one agency instead of every agency at
    # once.
    agg_max_row = await conn.fetchrow("SELECT MAX(date) AS d FROM agg_route_daily_dist WHERE agency_id = $1", agency_id)
    agg_max_day: date | None = agg_max_row["d"] if agg_max_row else None
    today_jst_midnight_utc = (
        datetime.now(_JST).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    )
    try:
        live_max_ts = await max_captured_at_before(ch, agency_id, today_jst_midnight_utc)
        live_max_day = None if live_max_ts is None else live_max_ts.astimezone(_JST).date()
    except Exception:
        # This probe backs ONLY is_stale below -- every other field here comes
        # from Postgres. A ClickHouse hiccup must degrade is_stale (via a None
        # live_max -- is_stale(agg_day, None) is "not stale": no completed day
        # / can't determine -> nothing owed), not fail the whole report.
        # Same degrade shape as pipeline.reports.network.compute_network_summary.
        _log.warning(
            "ClickHouse freshness probe failed for agency %s — degrading is_stale to False", agency_id, exc_info=True
        )
        live_max_day = None
    agency_is_stale = is_stale(agg_max_day, live_max_day)

    feed_row = await conn.fetchrow(
        "SELECT COALESCE(SUM(raw_samples), 0) AS raw, COALESCE(SUM(clamp_count), 0) AS clamp "
        "FROM agg_feed_health WHERE agency_id = $1 AND date BETWEEN $2 AND $3",
        agency_id,
        ctx.from_date,
        ctx.to_date,
    )
    raw = int(feed_row["raw"]) if feed_row else 0
    clamp = int(feed_row["clamp"]) if feed_row else 0
    clamp_pct = round(clamp / raw * 100, 2) if raw else None

    return {
        "on_time_pct": on_time_pct,
        "avg_delay_min": avg_delay_min,
        "samples": samples,
        "planned_trips": d["planned_trips"],
        "executed_trips": d["executed_trips"],
        "service_delivered_pct": d["service_delivered_pct"],
        "is_stale": agency_is_stale,
        "clamp_pct": clamp_pct,
    }


def shift_time_str(time_str: str, delta_sec: int) -> str:
    """Shift a zero-padded ``"HH:MM[:SS]"`` clock string by ``delta_sec``
    seconds, wrapping at 24h. Returns ``"HH:MM:SS"`` (always with seconds,
    regardless of whether the input had them), with a trailing
    ``"(+1日)"``/``"(-1日)"`` suffix when the shift crosses a calendar-day
    boundary. ``dep_delay`` is clamped to ``pipeline.db.MAX_PLAUSIBLE_DELAY_SEC``
    (±7200s, ±2h) everywhere it is read in this codebase, so this can only
    ever cross a single day boundary, never more. Public (unlike most of this
    module's helpers) so it can be unit-tested as pure logic, same rationale
    as e.g. ``pipeline.dwell_run``'s bucketize/percentile helpers.
    """
    parts = time_str.split(":")
    h, m = int(parts[0]), int(parts[1])
    s = int(parts[2]) if len(parts) > 2 else 0
    total = h * 3600 + m * 60 + s + delta_sec
    day_offset, rem = divmod(total, 86400)
    hh, rem2 = divmod(rem, 3600)
    mm, ss = divmod(rem2, 60)
    base = f"{hh:02d}:{mm:02d}:{ss:02d}"
    return base if day_offset == 0 else f"{base}({day_offset:+d}日)"


async def compute_delay_certificate(
    agency_id: int,
    ctx: RangeCtx,
    conn,
    ch,
    threshold_sec: int = DEFAULT_DELAY_CERTIFICATE_THRESHOLD_SEC,
    limit: int = 100,
) -> list[tuple]:
    """Every individual departure observation in ``ctx``'s range whose
    ``dep_delay`` STRICTLY EXCEEDS ``threshold_sec`` (``dep_delay >
    threshold_sec`` -- an observation exactly AT the threshold is excluded,
    matching the "exceeds" wording exactly rather than a boundary rounding
    artifact).

    Returns rows shaped ``(agency_name, route_code, service_type, date,
    scheduled_time, actual_time, dep_delay)``. ``actual_time`` is
    ``scheduled_time`` shifted by ``dep_delay`` seconds (see
    :func:`shift_time_str`). Ordered by ``(date, route_code,
    scheduled_time)`` and capped at ``limit`` rows, same "sane and
    reproducible cap" convention as every other report in this package's
    ``limit`` query param.

    Always a live ClickHouse scan (see module docstring) -- ``ch`` must be a
    real client.
    """
    if ch is None:
        raise RuntimeError("compute_delay_certificate requires a ClickHouse client")

    agency_row = await conn.fetchrow("SELECT agency_name FROM agencies WHERE agency_id = $1", agency_id)
    agency_name = agency_row["agency_name"] if agency_row else str(agency_id)

    cte_sql, ch_params = _dedup_cte_ch(ctx)
    result = await ch.query(
        f"WITH {cte_sql}\n"
        "SELECT route_code, service_type, date, scheduled_time, dep_delay\n"
        "FROM deduped\n"
        "WHERE scheduled_time IS NOT NULL AND dep_delay > {dc_threshold:Int32}\n"
        "ORDER BY date, route_code, scheduled_time\n"
        "LIMIT {dc_limit:UInt32}",
        parameters={
            "agency_id": agency_id,
            "dc_threshold": threshold_sec,
            "dc_limit": limit,
            **ch_params,
        },
    )
    rows: list[tuple] = []
    for route_code, service_type, d, scheduled_time, dep_delay in result.result_rows:
        actual_time = shift_time_str(scheduled_time, int(dep_delay))
        rows.append(
            (
                agency_name,
                route_code,
                service_type or None,
                d.isoformat(),
                scheduled_time,
                actual_time,
                int(dep_delay),
            )
        )
    return rows
