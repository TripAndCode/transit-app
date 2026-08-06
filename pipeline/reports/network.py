"""Cross-agency network summary: per-agency rollups over precomputed aggregates.

Read-only. One GROUP BY agency_id read per source, merged on the full agencies
list. Range-scoped (date window only — whole-agency comparison, so service/
time_band/dow/routes are not applied). Reuses the pure freshness rule.

``samples`` is the deduped observation count (from agg_route_daily_dist);
``raw_samples``/``clamp_count`` are raw poll counts (from agg_feed_health) —
different populations. ``clamp_pct`` is the implausible-reading ratio
(clamp_count / raw_samples; higher = worse), matching the #86 feed-health banner.
"""

from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from pipeline.cache import async_lru_cache
from pipeline.freshness import is_stale

_JST = ZoneInfo("Asia/Tokyo")

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

# Only a COMPLETED civil day counts (today's partial day is excluded). The
# cutoff MUST be a WHERE filter applied BEFORE maxOrNull — NOT a Python-side
# accept/reject of an unconditional maxOrNull(captured_at) — an earlier
# version of this query computed the max over the whole table and only
# accepted it if it was already < today's JST midnight, which silently
# produced `None` (never "the latest prior completed day") for any agency
# ingesting today too — i.e. every actively-ingesting agency, the normal
# healthy case. Filtering the rows first means the query's result already IS
# the latest completed day's max, with no further Python check needed. The
# cutoff itself is still computed in Python (JST midnight, converted to UTC)
# since ClickHouse's `captured_at` is stored as UTC.
_LIVE_MAX_ONE_CH_SQL = (
    "SELECT maxOrNull(captured_at) FROM updates "
    "WHERE agency_id = {agency_id:UInt16} AND captured_at < {today_jst_midnight_utc:DateTime64}"
)


@async_lru_cache(maxsize=64, ttl_seconds=300)
async def compute_network_summary(conn, ch, from_date: date, to_date: date) -> list[dict[str, Any]]:
    """Per-agency rollups over [from_date, to_date], ranked worst-avg-delay first."""
    agencies = await conn.fetch(
        "SELECT agency_id, agency_name FROM agencies WHERE deleted_at IS NULL ORDER BY agency_id"
    )
    perf = {r["agency_id"]: r for r in await conn.fetch(_PERF_SQL, from_date, to_date)}
    feed = {r["agency_id"]: r for r in await conn.fetch(_FEED_SQL, from_date, to_date)}
    agg_max = {r["agency_id"]: r["d"] for r in await conn.fetch(_AGG_MAX_SQL)}

    today_jst_midnight_utc = (
        datetime.now(_JST).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    )
    live_max: dict[int, "date | None"] = {}
    for a in agencies:
        aid = a["agency_id"]
        result = await ch.query(
            _LIVE_MAX_ONE_CH_SQL,
            parameters={"agency_id": aid, "today_jst_midnight_utc": today_jst_midnight_utc},
        )
        mx = result.result_rows[0][0]
        if mx is None:
            live_max[aid] = None
            continue
        mx_utc = mx.replace(tzinfo=timezone.utc) if mx.tzinfo is None else mx
        live_max[aid] = mx_utc.astimezone(_JST).date()

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
