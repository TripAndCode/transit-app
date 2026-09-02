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
(compute_ranking / route_trend_shift / compute_on_time) -- see
docs/superpowers/specs/2026-08-22-proactive-insight-panel-design.md for the
rationale behind this shape over a scored-blend or no-ranking rotation.

compute_suggestion() returns None when the agency has no analyzed data at
all, or when all remaining candidates at a rule level are excluded via the
exclude set.

Every rule pools compute_ranking's / compute_on_time's per-(route_code,
service_type) rows into one row per route_code (see
:func:`_pool_ranking_by_route` / :func:`_pool_on_time_by_route`) by
sample-weighting each row's OWN avg_min/p90_min/on_time_pct -- all three are
already rounded display figures (2dp / 2dp / 1dp respectively), not the
exact means the raw agg_route_daily_dist sums would give, so this pooling
step is itself only an approximation of the true pooled mean (compounding
whatever rounding compute_ranking/compute_on_time already did). Recovering
exactness here would mean compute_ranking/compute_on_time exposing their
raw sum_delay_sec/samples pair alongside the rounded tuple they already
return to the API/CSV/formatter layers -- out of scope for this module,
which only consumes their public return shape. See :func:`_anomaly_today`
for the one place this imprecision can flip a hard classification decision
rather than just reordering candidates.
"""

from __future__ import annotations

from datetime import timedelta

from api.range import RangeCtx
from api.triage import classify_route
from pipeline.query.tool_queries import route_trend_shift
from pipeline.query.tools import _summary
from pipeline.reports.rankings import compute_on_time, compute_ranking

TREND_SHIFT_CANDIDATE_LIMIT = 10
TREND_SHIFT_MIN_DELTA_MIN = 2.0
# Fixed, generous fetch sizes for the raw (route_code, service_type)-grain
# rows we pool from -- large enough that a handful of excluded/duplicate
# route_codes never starves the candidate pool. Not `len(exclude) + 1`: a
# single route can occupy several consecutive rows (one per service type),
# so that headroom assumption undercounted and could spuriously return None.
#
# Neither compute_ranking's fast path (_read_dist_with_hist) nor
# compute_on_time's (_read_dist_scalars, both in rankings.py) has a SQL
# LIMIT at the ctx shape this module always uses (time_band == "all"): both
# read every matching row from their agg table and only slice to `limit` in
# Python. So each constant must be at least the total number of qualifying
# pairs for an agency's trailing window, or a route's service-type rows can
# be truncated BEFORE the corresponding _pool_*_by_route sees them, silently
# corrupting the pooled figure for whichever route lands on the boundary.
# Real data tops out around ~880 pairs (agency 1); 5000 leaves ~5.7x
# headroom for both constants -- keep them equal rather than re-introducing
# a soft cap sitting near real utilization on just one of the two. Since
# there's no live-scan fallback at this ctx shape, a higher limit here costs
# nothing extra -- the full agg-table scan already happened either way.
RANKING_FETCH_LIMIT = 5000
ON_TIME_FALLBACK_FETCH_LIMIT = 5000

ExcludeSet = frozenset[tuple[str, str]]


async def _latest_analyzed_date(agency_id: int, conn):
    """Latest date with computed route aggregates for this agency.

    Mirrors ``api/routers/map.py``'s ``today_route_summary`` anchor: "today"
    means "as of the last analyze", not the wall clock, which normally lags
    it by at least a day. Anchoring the rule chain's "today"/baseline/week
    windows here (instead of ``jst_today()``) is what makes rule 1 ever see
    non-empty data in normal operation. Returns None for an agency with zero
    analyzed rows.
    """
    return await conn.fetchval(
        "SELECT MAX(date) FROM agg_route_daily_dist WHERE agency_id = $1",
        agency_id,
    )


def _pool_ranking_by_route(rows: list[tuple]) -> dict[str, dict]:
    """Pool compute_ranking's per-(route_code, service_type) rows into one
    row per route_code, sample-weighted.

    Every rule's underlying queries return one row per (route_code,
    service_type) pair (a route commonly has ~3 service-type variants on
    real data), but the rule chain reasons about routes. Pooling first makes
    that assumption true instead of threading service_type through every
    comparison.

    NOT exact: this sample-weights each row's own ``avg_min`` by its
    ``samples``, but ``compute_ranking``'s per-row ``avg_min`` is already
    ROUNDED to 2dp (``pipeline.reports.rankings._avg_min``'s
    ``Decimal(...).quantize(..., ROUND_HALF_UP)``), not the exact mean
    ``agg_route_daily_dist``'s raw ``sum_delay_sec`` would give -- weighting
    an already-rounded per-row mean by its own sample count does not recover
    the true pooled mean (each row can be off by up to half the rounding
    unit before weighting, e.g. two rows of `sum_delay_sec=1000, samples=21`
    and `sum_delay_sec=50, samples=21`: true combined mean is
    ``1050/42/60 ≈ 0.4167`` min, but pooling the rounded per-row means
    ``0.79`` and ``0.04`` gives ``(0.79*21 + 0.04*21)/42 = 0.415`` min).
    Recovering exactness would require ``compute_ranking`` to also expose
    the raw ``sum_delay_sec``/``samples`` pair behind its rounded tuple --
    that tuple shape is also the API/CSV/formatter/LLM-tool contract (see
    ``pipeline.query.formatter``/``pipeline.query.tools``), so widening it
    is out of scope for this module. ``p90_min`` pools the same
    (already-rounded, re-weighted) way; percentiles don't strictly compose
    across route/service groups regardless, so that part was never exact to
    begin with. This imprecision only reorders candidates for
    ``_trend_shift_this_week``/``_on_time_fallback``; see
    :func:`_anomaly_today` for where it can instead flip a hard
    classification.
    """
    sum_samples: dict[str, int] = {}
    sum_avg_weighted: dict[str, float] = {}
    sum_p90_weighted: dict[str, float] = {}
    sum_p90_samples: dict[str, int] = {}
    for route_code, _service, avg_min, _p50, p90_min, samples in rows:
        sum_samples[route_code] = sum_samples.get(route_code, 0) + samples
        sum_avg_weighted[route_code] = sum_avg_weighted.get(route_code, 0.0) + float(avg_min) * samples
        if p90_min is not None:
            sum_p90_weighted[route_code] = sum_p90_weighted.get(route_code, 0.0) + float(p90_min) * samples
            sum_p90_samples[route_code] = sum_p90_samples.get(route_code, 0) + samples

    pooled: dict[str, dict] = {}
    for route_code, samples in sum_samples.items():
        p90_samples = sum_p90_samples.get(route_code, 0)
        pooled[route_code] = {
            "avg_min": sum_avg_weighted[route_code] / samples,
            "p90_min": (sum_p90_weighted[route_code] / p90_samples) if p90_samples else None,
            "samples": samples,
        }
    return pooled


def _pool_on_time_by_route(rows: list[tuple]) -> dict[str, dict]:
    """Pool compute_on_time's per-(route_code, service_type) rows into one
    row per route_code, sample-weighted -- same convention (and same
    not-exact caveat: ``on_time_pct`` is already rounded to 1dp by
    ``compute_on_time`` before this re-weights it by ``samples``) as
    :func:`_pool_ranking_by_route`. Lower severity than that function's
    ``avg_min``/``p90_min`` case: this value only feeds
    :func:`_on_time_fallback`'s ranking order, never a hard threshold
    comparison."""
    sum_samples: dict[str, int] = {}
    sum_pct_weighted: dict[str, float] = {}
    for route_code, _service, on_time_pct, _avg_min, samples in rows:
        sum_samples[route_code] = sum_samples.get(route_code, 0) + samples
        sum_pct_weighted[route_code] = sum_pct_weighted.get(route_code, 0.0) + float(on_time_pct) * samples

    return {
        route_code: {"on_time_pct": sum_pct_weighted[route_code] / samples, "samples": samples}
        for route_code, samples in sum_samples.items()
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
    route never gets promoted into "anomaly"). Pools each side across
    service types first (see :func:`_pool_ranking_by_route`) so the
    comparison -- and the route+number the reason text names -- matches what
    an unfiltered report would show for that route.

    This is the one call site where :func:`_pool_ranking_by_route`'s
    known imprecision (see its docstring) can change an OUTCOME rather than
    just reorder candidates: ``avg_delay_sec``/``baseline_avg_sec``/
    ``baseline_p90_sec`` below round the already-imprecise pooled minutes to
    whole seconds a THIRD time before ``classify_route``'s strict
    ``avg_delay_sec > baseline_p90_sec`` comparison, so a route sitting
    within about a second of that boundary could in principle flip between
    "anomaly" and "watch" depending on how its service-type rows happened to
    split. This is accepted as a pre-existing, narrow-window edge case (not
    fixed here): a dedicated fix would need ``compute_ranking`` to expose
    exact raw sums to this module (see the docstring above), which is a
    change to a shared, widely-consumed return contract and so a separate
    piece of work from tightening this rule's own pooling.
    """
    today_rows = await compute_ranking(agency_id, today_ctx, conn, ch=ch, sort_order="desc", limit=RANKING_FETCH_LIMIT)
    if not today_rows:
        return None
    baseline_rows = await compute_ranking(
        agency_id, baseline_ctx, conn, ch=ch, sort_order="desc", limit=RANKING_FETCH_LIMIT
    )
    today_by_route = _pool_ranking_by_route(today_rows)
    baseline_by_route = _pool_ranking_by_route(baseline_rows)

    candidates = []  # (deviation_sec, route_code, today_avg_min)
    for route_code, today_pooled in today_by_route.items():
        if ("trend", route_code) in exclude:
            continue
        baseline_pooled = baseline_by_route.get(route_code)
        if baseline_pooled is None:
            continue
        avg_delay_sec = round(today_pooled["avg_min"] * 60)
        baseline_avg_sec = round(baseline_pooled["avg_min"] * 60)
        baseline_p90_sec = round(baseline_pooled["p90_min"] * 60) if baseline_pooled["p90_min"] is not None else None
        bucket, deviation_sec, _low_confidence = classify_route(
            avg_delay_sec, baseline_avg_sec, baseline_p90_sec, today_pooled["samples"]
        )
        if bucket == "anomaly" and deviation_sec is not None:
            candidates.append((deviation_sec, route_code, today_pooled["avg_min"]))

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
        "reason_text": _summary("suggest_reason_anomaly", lang=locale, route=route_code, avg_min=f"{today_avg:.1f}"),
        "severity": "notable",
        "from_date": today_ctx.from_date.isoformat(),
        "to_date": today_ctx.to_date.isoformat(),
    }


async def _trend_shift_this_week(agency_id, conn, ch, week_ctx, baseline_ctx, exclude, locale) -> dict | None:
    """Rule 2: among the routes with the worst pooled baseline delay, the one
    whose week-over-week pattern shifted the most partway through the
    trailing week. The candidate list is pooled by route_code first (see
    :func:`_pool_ranking_by_route`) so a route's several service-type rows
    don't crowd out other routes' one slot each in the top-N candidate list.
    """
    baseline_rows = await compute_ranking(
        agency_id, baseline_ctx, conn, ch=ch, sort_order="desc", limit=RANKING_FETCH_LIMIT
    )
    pooled = _pool_ranking_by_route(baseline_rows)
    candidates = sorted(pooled.items(), key=lambda kv: (-kv[1]["avg_min"], kv[0]))[:TREND_SHIFT_CANDIDATE_LIMIT]

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
            "suggest_reason_trend_shift", lang=locale, route=route_code, delta_min=f"{delta_min:+.1f}"
        ),
        "severity": "notable",
        "from_date": week_ctx.from_date.isoformat(),
        "to_date": week_ctx.to_date.isoformat(),
    }


async def _on_time_fallback(agency_id, conn, ch, week_ctx, exclude, locale) -> dict | None:
    """Rule 3: the worst on-time-rate route this week, pooled by route_code
    (see :func:`_pool_ranking_by_route`'s docstring / :func:`_pool_on_time_by_route`)
    so a route occupying several service-type rows can't spuriously starve
    the fallback of headroom, and so the % the reason text names matches
    what an unfiltered on_time report would show for that route --
    ``ON_TIME_FALLBACK_FETCH_LIMIT`` must cover every (route_code,
    service_type) row for the agency's trailing week for that to hold: fetch
    truncation happens before pooling, so a route split across the fetch
    boundary would otherwise get pooled from a partial subset of its rows
    and show a wrong (and, in the extreme, a wrong-route) result.
    """
    rows = await compute_on_time(agency_id, week_ctx, conn, ch=ch, limit=ON_TIME_FALLBACK_FETCH_LIMIT, sort_order="asc")
    pooled = _pool_on_time_by_route(rows)
    ranked = sorted(pooled.items(), key=lambda kv: (kv[1]["on_time_pct"], kv[0]))
    for route_code, stats in ranked:
        if ("on_time", route_code) in exclude:
            continue
        return {
            "report_type": "on_time",
            "route_code": route_code,
            "reason_text": _summary(
                "suggest_reason_on_time_fallback", lang=locale, route=route_code, pct=f"{stats['on_time_pct']:.0f}"
            ),
            "severity": "normal",
            "from_date": week_ctx.from_date.isoformat(),
            "to_date": week_ctx.to_date.isoformat(),
        }
    return None
