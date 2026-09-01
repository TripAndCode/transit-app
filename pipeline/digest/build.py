"""Assemble DigestData for one completed day. Read-only.

Per agency: read agg_route_daily(day), look up each route's baseline from a
route-grain aggregate of agg_route_stats (across service_types), bucket each
route with the pure triage.classify_route, keep the worsening ones
(anomaly/watch) as the top-N movers, and compute a sample-weighted headline.
A route-level baseline matters because agg_route_daily stores NULL service_type
as '' (COALESCE) while agg_route_stats has no '' row — a per-(route, service_type)
join would never match those NULL-service routes (real in agencies 9/10), so
they'd get no baseline and never become movers.
Adds feed-health (agg_feed_health) and freshness (check_agg_freshness).
"""

import logging
from datetime import date

from api.triage import classify_route
from pipeline.clickhouse import get_client
from pipeline.digest.models import AgencySection, DigestData, Mover
from pipeline.freshness import check_agg_freshness

_log = logging.getLogger(__name__)

TOP_MOVERS = 5

_AGENCIES_SQL = "SELECT agency_id, agency_name FROM agencies WHERE deleted_at IS NULL ORDER BY agency_id"

_DAY_ROUTES_SQL = """
    SELECT route_code, samples, sum_delay_sec
    FROM agg_route_daily
    WHERE agency_id = %(aid)s AND date = %(day)s
"""

# Route-grain baseline: aggregate agg_route_stats ACROSS service_types per route,
# weighted by the baseline's OWN samples. Keyed by route_code so a NULL-service
# ('') daily row still finds the route's overall baseline.
# base_avg_min pools each service_type's EXACT sum_delay_sec (raw seconds),
# dividing once at the end, rather than re-weighting each service_type's
# already-rounded avg_min — see pipeline/analyze.py's module docstring.
# Its numerator/denominator are both FILTERed to sum_delay_sec IS NOT NULL rows,
# the same defensive pattern base_p90_min already used below: analyze()-written
# rows always populate sum_delay_sec alongside samples together, but a row
# written any other way (a pre-migration-backfill historical row, a hand-seeded
# test fixture, or any future non-analyze() writer) could have samples set
# without it -- SUM() silently skips a null numerator term but NOT its row's
# sample count in the denominator, which would otherwise bias base_avg_min down
# whenever any contributing service_type is missing the column.
# base_p90_min's numerator/denominator are both FILTERed to the same
# p90_min IS NOT NULL rows, the same defensive pattern as base_avg_min above:
# `analyze()`'s own SQL can no longer produce a null p90_min alongside
# non-null samples for a live group (dep_delay is filtered non-null
# upstream, and analyze() wipes and rebuilds every row each run), but a
# pre-migration-backfill historical row, a hand-seeded test fixture, or any
# future non-analyze() writer could still leave one -- SUM() would then
# silently skip a null numerator term but NOT its row's sample count in the
# denominator, which would otherwise bias base_p90_min down whenever any
# contributing service_type's row is null this way. Percentiles don't
# compose exactly across buckets (unlike the mean), so base_p90_min stays a
# sample-weighted approximation of the rounded p90_min.
_ROUTE_BASELINE_SQL = """
    SELECT route_code,
           SUM(sum_delay_sec) FILTER (WHERE sum_delay_sec IS NOT NULL)::numeric
               / NULLIF(SUM(samples) FILTER (WHERE sum_delay_sec IS NOT NULL), 0) / 60.0 AS base_avg_min,
           SUM(p90_min * samples) FILTER (WHERE p90_min IS NOT NULL)
               / NULLIF(SUM(samples) FILTER (WHERE p90_min IS NOT NULL), 0) AS base_p90_min
    FROM agg_route_stats
    WHERE agency_id = %(aid)s AND samples IS NOT NULL
    GROUP BY route_code
"""

_FEED_HEALTH_SQL = """
    SELECT COALESCE(raw_samples, 0), COALESCE(clamp_count, 0)
    FROM agg_feed_health WHERE agency_id = %(aid)s AND date = %(day)s
"""


def _aggregate_by_route(rows):
    """Collapse per-(route, service_type) day rows to one weighted entry per route_code.

    Returns list of dicts: {route_code, avg_delay_sec, samples, sum_delay_sec}.
    A digest is per-route; multiple service_types on one day would otherwise yield
    duplicate-route_code movers (e.g. a typed + NULL-service row for the same route).
    The baseline is NOT part of this helper — it is looked up per route_code from a
    route-grain aggregate of agg_route_stats in build_digest.

    ``rows`` are tuples ``(route_code, samples, sum_delay_sec)`` from
    ``_DAY_ROUTES_SQL``; pools each service_type's EXACT sum_delay_sec and divides
    once at the end, rather than re-weighting each service_type's already-rounded
    per-row average — see pipeline/analyze.py's module docstring. This function's
    OWN ``avg_delay_sec`` output key is derived from the pooled sum, not read from
    a row. ``sum_delay_sec`` is also returned (exact, raw seconds) so build_digest's
    own further pooling across routes into an agency/network average can sum it
    directly instead of re-weighting this function's rounded per-route
    ``avg_delay_sec`` a second time.

    ``sum_delay_sec`` is nullable (migration 0028) — a row can have ``samples``
    set but ``sum_delay_sec`` still NULL (any ``agg_route_daily`` row analyze()
    hasn't rewritten since that migration). Such a row is skipped entirely
    (not just its numerator term, unlike SQL's SUM) so ``samples`` never counts
    a row this function's own Python ``+=`` can't otherwise add — matching the
    FILTER-both-sides pattern used everywhere else in this module.
    """
    acc: dict[str, dict] = {}
    order: list[str] = []
    for route_code, samples, sum_delay_sec in rows:
        if sum_delay_sec is None:
            continue
        e = acc.get(route_code)
        if e is None:
            e = {"route_code": route_code, "_delay_sum": 0, "samples": 0}
            acc[route_code] = e
            order.append(route_code)
        e["_delay_sum"] += sum_delay_sec
        e["samples"] += samples

    out: list[dict] = []
    for rc in order:
        e = acc[rc]
        samples = e["samples"]
        out.append(
            {
                "route_code": rc,
                "avg_delay_sec": round(e["_delay_sum"] / samples) if samples else 0,
                "samples": samples,
                "sum_delay_sec": e["_delay_sum"],
            }
        )
    return out


def build_digest(conn, target_day: date) -> DigestData:
    with conn.cursor() as cur:
        cur.execute(_AGENCIES_SQL)
        agencies = cur.fetchall()
    agency_ids = [a[0] for a in agencies]
    # Every other field in the digest comes from Postgres agg_* tables; this
    # ClickHouse probe backs ONLY the advisory `is_stale` flag per section. A
    # ClickHouse hiccup here (get_client() raising on a missing env var, or
    # check_agg_freshness's live-day query failing mid-run) must not kill the
    # whole (otherwise Postgres-only) digest — degrade and keep going, same
    # shape as pipeline.health.aggregate_freshness and
    # pipeline.reports.network.compute_network_summary's per-probe try/except.
    #
    # `staleness_known` is threaded through to DigestData/render_digest
    # separately from `stale_ids`: an empty `stale_ids` is ambiguous between
    # "probe ran, found nothing stale" and "probe failed" (every section's
    # is_stale ends up False either way), and render_digest's footer must not
    # collapse a failed probe into the affirmative "all agencies current"
    # line — this is the digest's only output surface, so that would be an
    # honestly-wrong claim, not just a missing one.
    staleness_known = True
    try:
        stale_ids = {s.agency_id for s in check_agg_freshness(conn, get_client(), agency_ids)}
    except Exception:
        _log.warning("ClickHouse freshness probe failed — digest staleness is unknown", exc_info=True)
        stale_ids = set()
        staleness_known = False

    sections: list[AgencySection] = []
    net_delay_weighted = 0.0
    net_samples = 0

    for aid, name in agencies:
        with conn.cursor() as cur:
            cur.execute(_DAY_ROUTES_SQL, {"aid": aid, "day": target_day})
            rows = cur.fetchall()
            cur.execute(_ROUTE_BASELINE_SQL, {"aid": aid})
            baseline_rows = cur.fetchall()
            cur.execute(_FEED_HEALTH_SQL, {"aid": aid, "day": target_day})
            fh = cur.fetchone()
        raw_samples, clamp_count = (fh[0], fh[1]) if fh else (0, 0)

        # route_code -> (base_avg_min, base_p90_min); minutes, converted later.
        baselines = {r[0]: (r[1], r[2]) for r in baseline_rows}

        if not rows:
            sections.append(
                AgencySection(aid, name, False, None, None, None, [], raw_samples, clamp_count, aid in stale_ids)
            )
            continue

        # One entry per route_code (collapse service_types) so a route never
        # produces duplicate movers / wastes top-5 slots.
        route_entries = _aggregate_by_route(rows)

        movers: list[Mover] = []
        delay_w = 0.0
        samples_sum = 0
        base_w = 0.0
        base_samples = 0
        # Today's delay weighted over ONLY the routes that also have a
        # baseline (same population/samples as base_w/base_samples) — kept
        # separate from delay_w/samples_sum (the full-population headline
        # avg_min) so delta_min compares the same route set on both sides.
        # Without this, a route with no baseline at all could swing the
        # headline delta even though every route WITH a real historical
        # baseline showed zero drift.
        matched_delay_w = 0.0
        for e in route_entries:
            avg_delay_sec = e["avg_delay_sec"]
            samples = e["samples"]
            # Route-grain baseline (keyed by route_code, so '' NULL-service rows
            # still match). Convert minutes -> seconds for classify_route.
            base_avg_min, base_p90_min = baselines.get(e["route_code"], (None, None))
            # Unrounded (but cast to float -- base_avg_min comes back as
            # decimal.Decimal from the ::numeric SQL cast, which can't mix with
            # base_p90_sec's plain float/int below): classify_route accepts a
            # float baseline, and Mover's own display re-rounds to 1 decimal at
            # render time (see below) — rounding to whole seconds here would
            # only lose precision before this value gets weighted into base_w,
            # the same rounded-then-reweight pattern this diff eliminates
            # everywhere else.
            base_avg_sec = float(base_avg_min) * 60 if base_avg_min is not None else None
            base_p90_sec = round(base_p90_min * 60) if base_p90_min is not None else None
            bucket, deviation_sec, low_conf = classify_route(avg_delay_sec, base_avg_sec, base_p90_sec, samples)
            # delay_w/matched_delay_w pool EXACT per-route raw-seconds sums across
            # routes (same underlying population: this agency's trips today), so
            # they sum e["sum_delay_sec"] directly rather than re-weighting the
            # already-rounded e["avg_delay_sec"] by samples a second time.
            delay_w += e["sum_delay_sec"]
            samples_sum += samples
            # Weight each route's route-grain baseline by that route's TODAY
            # samples so the headline delta stays apples-to-apples with today's avg.
            if base_avg_sec is not None:
                base_w += base_avg_sec * samples
                base_samples += samples
                matched_delay_w += e["sum_delay_sec"]
            if bucket in ("anomaly", "watch"):
                movers.append(
                    Mover(
                        route_code=e["route_code"],
                        avg_delay_min=round(avg_delay_sec / 60, 1),
                        baseline_avg_min=round(base_avg_sec / 60, 1) if base_avg_sec is not None else None,
                        deviation_min=round((deviation_sec or 0) / 60, 1),
                        bucket=bucket,
                        low_confidence=low_conf,
                    )
                )
        # Two-pass stable sort: pre-sort by route_code so ties on deviation_min
        # break deterministically in ascending route_code order regardless of
        # `reverse` — `route_entries` is insertion-ordered from a dict keyed
        # by route_code, with no guaranteed ordering of its own.
        movers.sort(key=lambda m: m.route_code)
        movers.sort(key=lambda m: m.deviation_min, reverse=True)

        avg_min = round((delay_w / samples_sum) / 60, 1) if samples_sum else None
        base_min = round((base_w / base_samples) / 60, 1) if base_samples else None
        matched_avg_min = round((matched_delay_w / base_samples) / 60, 1) if base_samples else None
        delta_min = (
            round(matched_avg_min - base_min, 1) if (matched_avg_min is not None and base_min is not None) else None
        )

        sections.append(
            AgencySection(
                aid,
                name,
                True,
                avg_min,
                base_min,
                delta_min,
                movers[:TOP_MOVERS],
                raw_samples,
                clamp_count,
                aid in stale_ids,
            )
        )
        net_delay_weighted += delay_w
        net_samples += samples_sum

    network_avg = round((net_delay_weighted / net_samples) / 60, 1) if net_samples else None
    return DigestData(
        target_day=target_day,
        network_avg_delay_min=network_avg,
        sections=sections,
        staleness_known=staleness_known,
    )
