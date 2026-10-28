"""Rule-chain logic for the Analysis tab's Insight Panel.

Fixed-priority chain, evaluated over a trailing-week window anchored to the
agency's own latest analyzed date (NOT the wall clock -- see
``_latest_analyzed_date``), independent of whatever filters the Analysis tab
UI currently has selected: (1) a route whose latest day's avg delay is
anomalously worse than its own trailing baseline (per the same
baseline-relative classifier the 最新観測 tab uses, ``api.triage.classify_route``),
(2) failing that, a route whose delay pattern shifted partway through the
trailing week, (3) failing that, the single worst on-time-rate route this
week. No LLM; every branch is a composition of existing report primitives
(compute_ranking / route_trend_shift / compute_on_time).

compute_suggestion() returns None when the agency has no analyzed data at
all, or when all remaining candidates at a rule level are excluded via the
exclude set.

compute_ranking / compute_on_time return one row per route, over every
observation of it in the window, so each rule reads their rows keyed by
route_code (:func:`_ranking_by_route` / :func:`_on_time_by_route`) and names
the same figure an unfiltered report shows for that route.
"""

from __future__ import annotations

from datetime import date, timedelta

from api.range import RangeCtx
from api.triage import classify_route
from pipeline.query.labels import dow_label
from pipeline.query.tool_queries import route_trend_shift
from pipeline.query.tools import _summary
from pipeline.reports.rankings import compute_on_time, compute_ranking

TREND_SHIFT_CANDIDATE_LIMIT = 10
TREND_SHIFT_MIN_DELTA_MIN = 2.0
# Fetch every route: each rule filters its candidates after the fetch
# (exclude set, baseline match, rule thresholds), and rule 1 pairs two
# fetches by route, so a cut before that could drop the route a rule should
# name. 5000 sits well above any agency's route count. Neither
# compute_ranking's fast path (_read_dist_with_hist) nor compute_on_time's
# (_read_dist_scalars, both in rankings.py) has a SQL LIMIT at the ctx shape
# this module always uses (time_band == "all"): both read every matching row
# from their agg table and only slice to `limit` in Python, so a higher limit
# costs nothing extra.
RANKING_FETCH_LIMIT = 5000
ON_TIME_FALLBACK_FETCH_LIMIT = 5000

ExcludeSet = frozenset[tuple[str, str]]


def _day_text(day: date, locale: str) -> str:
    """A day as the reason text names it ("10/23（金）", "Fri 10/23")."""
    return _summary("suggest_day", lang=locale, month=day.month, day=day.day, dow=dow_label(day.isoweekday(), locale))


def _span_text(ctx: RangeCtx, locale: str) -> str:
    return _summary(
        "suggest_span", lang=locale, first=_day_text(ctx.from_date, locale), last=_day_text(ctx.to_date, locale)
    )


async def _latest_analyzed_date(agency_id: int, conn):
    """Latest date with computed route aggregates for this agency.

    "Today" here means "as of the last analyze", not the wall clock, which
    normally lags it by at least a day. Anchoring the rule chain's
    "today"/baseline/week windows here (instead of ``jst_today()``) is what
    makes rule 1 ever see non-empty data in normal operation. Returns None
    for an agency with zero analyzed rows.
    """
    return await conn.fetchval(
        "SELECT MAX(date) FROM agg_route_daily_dist WHERE agency_id = $1",
        agency_id,
    )


def _ranking_by_route(rows: list[tuple]) -> dict[str, dict]:
    """compute_ranking's rows, one per route, keyed by route_code."""
    return {
        route_code: {
            "avg_min": float(avg_min),
            "p90_min": None if p90_min is None else float(p90_min),
            "samples": samples,
        }
        for route_code, _service, avg_min, _p50, p90_min, samples in rows
    }


def _on_time_by_route(rows: list[tuple]) -> dict[str, dict]:
    """compute_on_time's rows, one per route, keyed by route_code."""
    return {
        route_code: {"on_time_pct": float(on_time_pct), "samples": samples}
        for route_code, _service, on_time_pct, _avg_min, samples in rows
    }


async def compute_suggestion(
    agency_id: int,
    conn,
    ch,
    exclude: ExcludeSet = frozenset(),
    locale: str = "ja",
) -> dict | None:
    latest_date = await _latest_analyzed_date(agency_id, conn)
    if latest_date is None:
        return None

    today_ctx = RangeCtx(from_date=latest_date, to_date=latest_date)
    baseline_ctx = RangeCtx(from_date=latest_date - timedelta(days=7), to_date=latest_date - timedelta(days=1))
    week_ctx = RangeCtx(from_date=latest_date - timedelta(days=6), to_date=latest_date)

    candidate = await _anomaly_today(agency_id, conn, ch, today_ctx, baseline_ctx, exclude, locale)
    if candidate:
        return candidate

    candidate = await _trend_shift_this_week(agency_id, conn, ch, week_ctx, baseline_ctx, exclude, locale)
    if candidate:
        return candidate

    return await _on_time_fallback(agency_id, conn, ch, week_ctx, exclude, locale)


async def _anomaly_today(agency_id, conn, ch, today_ctx, baseline_ctx, exclude, locale) -> dict | None:
    """Rule 1: a route whose latest-day avg delay is a real anomaly against
    its own trailing baseline, per ``api.triage.classify_route`` -- the same
    documented, unit-tested judgment the 最新観測 tab uses (anomaly = today's
    avg > baseline p90), including its low-sample-confidence gate (a thin
    route never gets promoted into "anomaly"). Each side is one row per route,
    so the comparison -- and the figure the reason text names --
    matches what an unfiltered report would show for that route.

    ``avg_delay_sec``/``baseline_avg_sec``/``baseline_p90_sec`` round
    compute_ranking's 2-dp minutes to whole seconds before
    ``classify_route``'s strict ``avg_delay_sec > baseline_p90_sec``
    comparison, so a route within about a second of that boundary can land
    on either side of it.
    """
    today_rows = await compute_ranking(agency_id, today_ctx, conn, ch=ch, sort_order="desc", limit=RANKING_FETCH_LIMIT)
    if not today_rows:
        return None
    baseline_rows = await compute_ranking(
        agency_id, baseline_ctx, conn, ch=ch, sort_order="desc", limit=RANKING_FETCH_LIMIT
    )
    today_by_route = _ranking_by_route(today_rows)
    baseline_by_route = _ranking_by_route(baseline_rows)

    candidates = []  # (deviation_sec, route_code, today_avg_min)
    for route_code, today in today_by_route.items():
        if ("trend", route_code) in exclude:
            continue
        baseline = baseline_by_route.get(route_code)
        if baseline is None:
            continue
        avg_delay_sec = round(today["avg_min"] * 60)
        baseline_avg_sec = round(baseline["avg_min"] * 60)
        baseline_p90_sec = round(baseline["p90_min"] * 60) if baseline["p90_min"] is not None else None
        bucket, deviation_sec, _low_confidence = classify_route(
            avg_delay_sec, baseline_avg_sec, baseline_p90_sec, today["samples"]
        )
        if bucket == "anomaly" and deviation_sec is not None:
            candidates.append((deviation_sec, route_code, today["avg_min"]))

    if not candidates:
        return None
    # Highest deviation first; route_code ascending breaks ties -- same
    # convention as rules 2/3's `sorted(..., key=lambda kv: (-metric, kv[0]))`,
    # rather than leaving ties to today_by_route's incidental insertion order.
    candidates.sort(key=lambda c: (-c[0], c[1]))
    _deviation_sec, route_code, today_avg = candidates[0]
    return {
        "report_type": "trend",
        "route_code": route_code,
        "reason_text": _summary(
            "suggest_reason_anomaly",
            lang=locale,
            day=_day_text(today_ctx.to_date, locale),
            avg_min=f"{today_avg:.1f}",
        ),
        "severity": "notable",
        "from_date": today_ctx.from_date.isoformat(),
        "to_date": today_ctx.to_date.isoformat(),
    }


async def _trend_shift_this_week(agency_id, conn, ch, week_ctx, baseline_ctx, exclude, locale) -> dict | None:
    """Rule 2: among the routes with the worst baseline delay, the one whose
    week-over-week pattern shifted the most partway through the trailing week.
    """
    baseline_rows = await compute_ranking(
        agency_id, baseline_ctx, conn, ch=ch, sort_order="desc", limit=RANKING_FETCH_LIMIT
    )
    by_route = _ranking_by_route(baseline_rows)
    candidates = sorted(by_route.items(), key=lambda kv: (-kv[1]["avg_min"], kv[0]))[:TREND_SHIFT_CANDIDATE_LIMIT]

    best = None  # (abs_delta, route_code, delta_min)
    for route_code, _stats in candidates:
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
            # delta_min is second-half minus first-half, so a positive shift is a worsening.
            "suggest_reason_trend_worsened" if delta_min > 0 else "suggest_reason_trend_improved",
            lang=locale,
            span=_span_text(week_ctx, locale),
            delta_min=f"{delta_min:+.1f}",
        ),
        "severity": "notable",
        "from_date": week_ctx.from_date.isoformat(),
        "to_date": week_ctx.to_date.isoformat(),
    }


async def _on_time_fallback(agency_id, conn, ch, week_ctx, exclude, locale) -> dict | None:
    """Rule 3: the worst on-time-rate route this week that the exclude set
    leaves, at the % an unfiltered on_time report shows for that route."""
    rows = await compute_on_time(agency_id, week_ctx, conn, ch=ch, limit=ON_TIME_FALLBACK_FETCH_LIMIT, sort_order="asc")
    ranked = sorted(_on_time_by_route(rows).items(), key=lambda kv: (kv[1]["on_time_pct"], kv[0]))
    for route_code, stats in ranked:
        if ("on_time", route_code) in exclude:
            continue
        return {
            "report_type": "on_time",
            "route_code": route_code,
            "reason_text": _summary(
                "suggest_reason_on_time_fallback",
                lang=locale,
                span=_span_text(week_ctx, locale),
                pct=f"{stats['on_time_pct']:.0f}",
            ),
            "severity": "normal",
            "from_date": week_ctx.from_date.isoformat(),
            "to_date": week_ctx.to_date.isoformat(),
        }
    return None
