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
    SELECT route_code, avg_delay_sec, samples
    FROM agg_route_daily
    WHERE agency_id = %(aid)s AND date = %(day)s
"""

# Route-grain baseline: aggregate agg_route_stats ACROSS service_types per route,
# weighted by the baseline's OWN samples. Keyed by route_code so a NULL-service
# ('') daily row still finds the route's overall baseline.
_ROUTE_BASELINE_SQL = """
    SELECT route_code,
           SUM(avg_min * samples) / NULLIF(SUM(samples), 0) AS base_avg_min,
           SUM(p90_min * samples) / NULLIF(SUM(samples), 0) AS base_p90_min
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

    Returns list of dicts: {route_code, avg_delay_sec, samples}.
    A digest is per-route; multiple service_types on one day would otherwise yield
    duplicate-route_code movers (e.g. a typed + NULL-service row for the same route).
    The baseline is NOT part of this helper — it is looked up per route_code from a
    route-grain aggregate of agg_route_stats in build_digest.

    ``rows`` are tuples ``(route_code, avg_delay_sec, samples)`` from
    ``_DAY_ROUTES_SQL``; avg_delay_sec is sample-weighted across service_types.
    """
    acc: dict[str, dict] = {}
    order: list[str] = []
    for route_code, avg_delay_sec, samples in rows:
        e = acc.get(route_code)
        if e is None:
            e = {"route_code": route_code, "_delay_w": 0.0, "samples": 0}
            acc[route_code] = e
            order.append(route_code)
        e["_delay_w"] += avg_delay_sec * samples
        e["samples"] += samples

    out: list[dict] = []
    for rc in order:
        e = acc[rc]
        samples = e["samples"]
        out.append(
            {
                "route_code": rc,
                "avg_delay_sec": round(e["_delay_w"] / samples) if samples else 0,
                "samples": samples,
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
    # whole (otherwise Postgres-only) digest — degrade to "no stale agencies
    # known" and keep going, same shape as pipeline.health.aggregate_freshness
    # and pipeline.reports.network.compute_network_summary's per-probe try/except.
    try:
        stale_ids = {s.agency_id for s in check_agg_freshness(conn, get_client(), agency_ids)}
    except Exception:
        _log.warning("ClickHouse freshness probe failed — digest omits staleness flags", exc_info=True)
        stale_ids = set()

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
            base_avg_sec = round(base_avg_min * 60) if base_avg_min is not None else None
            base_p90_sec = round(base_p90_min * 60) if base_p90_min is not None else None
            bucket, deviation_sec, low_conf = classify_route(avg_delay_sec, base_avg_sec, base_p90_sec, samples)
            delay_w += avg_delay_sec * samples
            samples_sum += samples
            # Weight each route's route-grain baseline by that route's TODAY
            # samples so the headline delta stays apples-to-apples with today's avg.
            if base_avg_sec is not None:
                base_w += base_avg_sec * samples
                base_samples += samples
                matched_delay_w += avg_delay_sec * samples
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
    return DigestData(target_day=target_day, network_avg_delay_min=network_avg, sections=sections)
