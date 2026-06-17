"""Assemble DigestData for one completed day. Read-only.

Per agency: join agg_route_daily(day) with agg_route_stats (baseline) per route,
bucket each route with the pure triage.classify_route, keep the worsening ones
(anomaly/watch) as the top-N movers, and compute a sample-weighted headline.
Adds feed-health (agg_feed_health) and freshness (check_agg_freshness).
"""

from datetime import date

from api.triage import classify_route
from pipeline.digest.models import AgencySection, DigestData, Mover
from pipeline.freshness import check_agg_freshness

TOP_MOVERS = 5

_AGENCIES_SQL = "SELECT agency_id, agency_name FROM agencies ORDER BY agency_id"

_DAY_ROUTES_SQL = """
    SELECT d.route_code, d.avg_delay_sec, d.samples,
           b.avg_min AS baseline_avg_min, b.p90_min AS baseline_p90_min
    FROM agg_route_daily d
    LEFT JOIN agg_route_stats b
      ON b.agency_id = %(aid)s
     AND b.route_code = d.route_code
     AND b.service_type = d.service_type
    WHERE d.agency_id = %(aid)s AND d.date = %(day)s
"""

_FEED_HEALTH_SQL = """
    SELECT COALESCE(raw_samples, 0), COALESCE(clamp_count, 0)
    FROM agg_feed_health WHERE agency_id = %(aid)s AND date = %(day)s
"""


def build_digest(conn, target_day: date) -> DigestData:
    with conn.cursor() as cur:
        cur.execute(_AGENCIES_SQL)
        agencies = cur.fetchall()
    agency_ids = [a[0] for a in agencies]
    stale_ids = {s.agency_id for s in check_agg_freshness(conn, agency_ids)}

    sections: list[AgencySection] = []
    net_delay_weighted = 0.0
    net_samples = 0

    for aid, name in agencies:
        with conn.cursor() as cur:
            cur.execute(_DAY_ROUTES_SQL, {"aid": aid, "day": target_day})
            rows = cur.fetchall()
            cur.execute(_FEED_HEALTH_SQL, {"aid": aid, "day": target_day})
            fh = cur.fetchone()
        raw_samples, clamp_count = (fh[0], fh[1]) if fh else (0, 0)

        if not rows:
            sections.append(
                AgencySection(aid, name, False, None, None, None, [], raw_samples, clamp_count, aid in stale_ids)
            )
            continue

        movers: list[Mover] = []
        delay_w = 0.0
        samples_sum = 0
        base_w = 0.0
        base_samples = 0
        for route_code, avg_delay_sec, samples, base_avg_min, base_p90_min in rows:
            base_avg_sec = round(base_avg_min * 60) if base_avg_min is not None else None
            base_p90_sec = round(base_p90_min * 60) if base_p90_min is not None else None
            bucket, deviation_sec, low_conf = classify_route(avg_delay_sec, base_avg_sec, base_p90_sec, samples)
            delay_w += avg_delay_sec * samples
            samples_sum += samples
            if base_avg_sec is not None:
                base_w += base_avg_sec * samples
                base_samples += samples
            if bucket in ("anomaly", "watch"):
                movers.append(
                    Mover(
                        route_code=route_code,
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
        delta_min = round(avg_min - base_min, 1) if (avg_min is not None and base_min is not None) else None

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
