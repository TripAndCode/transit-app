"""Cross-agency network summary: per-agency rollups over precomputed aggregates.

Read-only. One GROUP BY agency_id read per source, merged on the full agencies
list. Range-scoped (date window only — whole-agency comparison, so service/
time_band/dow/routes are not applied). Reuses the pure freshness rule.

``samples`` is the deduped observation count (from agg_route_daily_dist);
``raw_samples``/``clamp_count`` are raw poll counts (from agg_feed_health) —
different populations. ``clamp_pct`` is the implausible-reading ratio
(clamp_count / raw_samples; higher = worse), matching the #86 feed-health banner.
"""

from datetime import date
from typing import Any

from pipeline.cache import async_lru_cache
from pipeline.freshness import is_stale

_PERF_SQL = """
    SELECT agency_id, SUM(samples) AS n, SUM(sum_delay_sec) AS sd, SUM(on_time_count) AS ot,
           MIN(date) AS data_from, MAX(date) AS data_to
    FROM agg_route_daily_dist
    WHERE date BETWEEN $1 AND $2
    GROUP BY agency_id
"""

_FEED_SQL = """
    SELECT agency_id, SUM(raw_samples) AS raw, SUM(clamp_count) AS clamp
    FROM agg_feed_health
    WHERE date BETWEEN $1 AND $2
    GROUP BY agency_id
"""

_AGG_MAX_SQL = "SELECT agency_id, MAX(date) AS d FROM agg_route_daily_dist GROUP BY agency_id"

_LIVE_MAX_ONE_SQL = """
    SELECT (MAX(captured_at) AT TIME ZONE 'Asia/Tokyo')::date AS d
    FROM updates
    WHERE agency_id = $1
      AND captured_at < (date_trunc('day', now() AT TIME ZONE 'Asia/Tokyo'))
                        AT TIME ZONE 'Asia/Tokyo'
"""


@async_lru_cache(maxsize=64, ttl_seconds=300)
async def compute_network_summary(conn, from_date: date, to_date: date) -> list[dict[str, Any]]:
    """Per-agency rollups over [from_date, to_date], ranked worst-avg-delay first."""
    agencies = await conn.fetch(
        "SELECT agency_id, agency_name FROM agencies WHERE deleted_at IS NULL ORDER BY agency_id"
    )
    perf = {r["agency_id"]: r for r in await conn.fetch(_PERF_SQL, from_date, to_date)}
    feed = {r["agency_id"]: r for r in await conn.fetch(_FEED_SQL, from_date, to_date)}
    agg_max = {r["agency_id"]: r["d"] for r in await conn.fetch(_AGG_MAX_SQL)}

    live_max: dict[int, "date | None"] = {}
    for a in agencies:
        row = await conn.fetchrow(_LIVE_MAX_ONE_SQL, a["agency_id"])
        live_max[a["agency_id"]] = row["d"] if row else None

    rows: list[dict[str, Any]] = []
    for a in agencies:
        aid = a["agency_id"]
        p = perf.get(aid)
        n = int(p["n"]) if p and p["n"] else 0
        avg_delay_min = round(float(p["sd"]) / n / 60, 1) if (p and n) else None
        on_time_pct = round(p["ot"] / n * 100, 1) if p and n else None
        f = feed.get(aid)
        raw = int(f["raw"]) if f and f["raw"] else 0
        clamp = int(f["clamp"]) if f and f["clamp"] else 0
        clamp_pct = round(clamp / raw * 100, 2) if raw else None
        data_from = p["data_from"].isoformat() if (p and p["data_from"]) else None
        data_to = p["data_to"].isoformat() if (p and p["data_to"]) else None
        rows.append(
            {
                "agency_id": aid,
                "agency_name": a["agency_name"],
                "avg_delay_min": avg_delay_min,
                "on_time_pct": on_time_pct,
                "samples": n,
                "raw_samples": raw,
                "clamp_count": clamp,
                "clamp_pct": clamp_pct,
                "is_stale": is_stale(agg_max.get(aid), live_max.get(aid)),
                "data_from": data_from,
                "data_to": data_to,
            }
        )
    rows.sort(key=lambda r: (r["avg_delay_min"] is None, -(r["avg_delay_min"] or 0.0)))
    return rows
