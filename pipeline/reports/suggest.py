"""Rule-chain logic for the Analysis tab's Insight Panel.

Fixed-priority chain, evaluated over a trailing-week window independent of
whatever filters the Analysis tab UI currently has selected: (1) a route
whose today's avg delay is anomalously worse than its own trailing
baseline, (2) failing that, a route whose delay pattern shifted partway
through the trailing week, (3) failing that, the single worst on-time-rate
route this week. No LLM; every branch is a composition of existing report
primitives (compute_ranking / route_trend_shift / compute_on_time) -- see
docs/superpowers/specs/2026-08-22-proactive-insight-panel-design.md for the
rationale behind this shape over a scored-blend or no-ranking rotation.
"""

from __future__ import annotations

from datetime import timedelta

from api.range import RangeCtx, jst_today
from pipeline.query.tool_queries import route_trend_shift
from pipeline.query.tools import _summary
from pipeline.reports.rankings import compute_on_time, compute_ranking

ANOMALY_RATIO_THRESHOLD = 1.5
ANOMALY_MIN_DELTA_MIN = 2.0
TREND_SHIFT_CANDIDATE_LIMIT = 10
TREND_SHIFT_MIN_DELTA_MIN = 2.0

ExcludeSet = frozenset[tuple[str, str]]


async def compute_suggestion(
    agency_id: int,
    conn,
    ch,
    exclude: ExcludeSet = frozenset(),
    locale: str = "ja",
) -> dict | None:
    today = jst_today()
    today_ctx = RangeCtx(from_date=today, to_date=today)
    baseline_ctx = RangeCtx(from_date=today - timedelta(days=7), to_date=today - timedelta(days=1))
    week_ctx = RangeCtx(from_date=today - timedelta(days=6), to_date=today)

    candidate = await _anomaly_today(agency_id, conn, ch, today_ctx, baseline_ctx, exclude, locale)
    if candidate:
        return candidate

    candidate = await _trend_shift_this_week(agency_id, conn, ch, week_ctx, baseline_ctx, exclude, locale)
    if candidate:
        return candidate

    return await _on_time_fallback(agency_id, conn, ch, week_ctx, exclude, locale)


async def _anomaly_today(agency_id, conn, ch, today_ctx, baseline_ctx, exclude, locale) -> dict | None:
    today_rows = await compute_ranking(agency_id, today_ctx, conn, ch, sort_order="desc", limit=1000)
    if not today_rows:
        return None
    baseline_rows = await compute_ranking(agency_id, baseline_ctx, conn, ch, sort_order="desc", limit=1000)
    baseline_by_route = {r[0]: r[2] for r in baseline_rows}  # route_code -> avg_min

    best = None  # (ratio, route_code, today_avg)
    for route_code, _service, today_avg, _p50, _p90, _samples in today_rows:
        if ("trend", route_code) in exclude:
            continue
        baseline_avg = baseline_by_route.get(route_code)
        if not baseline_avg or baseline_avg <= 0:
            continue
        delta = today_avg - baseline_avg
        ratio = today_avg / baseline_avg
        if ratio >= ANOMALY_RATIO_THRESHOLD and delta >= ANOMALY_MIN_DELTA_MIN:
            if best is None or ratio > best[0]:
                best = (ratio, route_code, today_avg)

    if best is None:
        return None
    _ratio, route_code, today_avg = best
    return {
        "report_type": "trend",
        "route_code": route_code,
        "reason_text": _summary(
            "suggest_reason_anomaly", lang=locale, route=route_code, avg_min=f"{today_avg:.1f}"
        ),
        "severity": "notable",
    }


async def _trend_shift_this_week(
    agency_id, conn, ch, week_ctx, baseline_ctx, exclude, locale
) -> dict | None:
    baseline_rows = await compute_ranking(
        agency_id, baseline_ctx, conn, ch, sort_order="desc", limit=TREND_SHIFT_CANDIDATE_LIMIT
    )
    best = None  # (abs_delta, route_code, delta_min)
    for route_code, _service, _avg, _p50, _p90, _samples in baseline_rows:
        if ("trend", route_code) in exclude:
            continue
        result = await route_trend_shift(agency_id, week_ctx, conn, ch, route=route_code)
        if result is None:
            continue
        delta_min = result["delta_min"]
        if abs(delta_min) >= TREND_SHIFT_MIN_DELTA_MIN:
            if best is None or abs(delta_min) > best[0]:
                best = (abs(delta_min), route_code, delta_min)

    if best is None:
        return None
    _abs_delta, route_code, delta_min = best
    return {
        "report_type": "trend",
        "route_code": route_code,
        "reason_text": _summary(
            "suggest_reason_trend_shift", lang=locale, route=route_code, delta_min=f"{delta_min:+.1f}"
        ),
        "severity": "notable",
    }


async def _on_time_fallback(agency_id, conn, ch, week_ctx, exclude, locale) -> dict | None:
    rows = await compute_on_time(agency_id, week_ctx, conn, ch, limit=len(exclude) + 1, sort_order="asc")
    # Extract route codes already in the exclude set (any report type)
    excluded_routes = {r for _report_type, r in exclude}
    for route_code, _service, on_time_pct, _avg, _samples in rows:
        if route_code in excluded_routes:
            continue
        return {
            "report_type": "on_time",
            "route_code": route_code,
            "reason_text": _summary(
                "suggest_reason_on_time_fallback", lang=locale, route=route_code, pct=f"{on_time_pct:.0f}"
            ),
            "severity": "normal",
        }
    return None
