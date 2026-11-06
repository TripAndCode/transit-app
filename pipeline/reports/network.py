"""Cross-agency network summary: per-agency rollups over precomputed aggregates.

Read-only. One GROUP BY agency_id read per source, merged on the full agencies
list. Range-scoped (date window only — whole-agency comparison, so service/
time_band/dow/routes are not applied). Reuses the pure freshness rule.

Only the agencies whose data supports a comparison are ranked:
`pipeline.reports.comparability` decides ``comparable`` and the first
``comparable_reason`` it fails from ``coverage_pct`` (``observed_trips`` of
``coverage_planned_trips``), the on-time interval ``ci_low``/``ci_high``,
``clamp_pct`` and the agency's ingest strategy. Comparable rows come first,
worst ``avg_delay_min`` first; the rest follow in name order, never by a
figure the data cannot support. Coverage counts the planned days up to
``coverage_through``, the newest day any agency has observations for in the
range: a later day is one the aggregates have not reached for anyone yet,
which is no one feed's missing trips, while a gap inside that span counts
against the feed that has it.

``samples`` is the deduped observation count (from agg_route_daily_dist);
``raw_samples``/``clamp_count`` are raw poll counts (from agg_feed_health) —
different populations. ``clamp_pct`` is the implausible-reading ratio
(clamp_count / raw_samples; higher = worse) shown by the feed-health banner.

``planned_trips``/``executed_trips``/``service_delivered_pct`` come from
`pipeline.reports.service_delivered.compute_service_delivered_by_agency` —
see that module for the executed/planned definition and why the latter two
read `None` ("not available") for an agency whose feed doesn't populate
`schedule_relationship_trip`, or has reported no cancellation in it.
``covered_days`` of ``window_days`` is how many of the range's days a
timetable plans (a day's saved count, or the loaded timetable's); all three
count only those, so a row whose ``covered_days`` falls short is comparable
to its neighbours only with that note beside it.
When those figures cannot be read at all, every agency reads as one with no
plan (0 planned trips and covered days, ``None`` rates) whose
``delivered_missing`` is ``"unreadable"``, so the board never prints that 0 as
a plan, and the rest of the board stands.

``has_ridership_weights``/``weighted_on_time_pct`` come from
`pipeline.reports.ridership.compute_ridership_weighted_on_time_by_agency` —
see that module for the manually-populated `ridership_weights` table and why
an agency with no rows there reads `has_ridership_weights=False` and
`weighted_on_time_pct=None`, never a value computed with an implicit uniform
weight.

``static_version_id``/``planned_trip_count``/``planned_vehicle_km``/
``vehicle_km_delivered_pct`` come from
`pipeline.reports.supply.compute_supply_metrics_by_agency` — see that module
for why these describe the current static-feed version's schedule
definition rather than a date-range total, and why
``vehicle_km_delivered_pct`` reads `None` whenever either the vehicle-km
figure or the executed/planned trip ratio isn't available for this agency.
"""

import logging
from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from api.clickhouse import max_captured_at_before_by_agency
from pipeline.cache import async_lru_cache
from pipeline.freshness import is_stale
from pipeline.reports.comparability import (
    COMPARABLE_INGEST_STRATEGIES,
    assess,
    observation_coverage,
    on_time_interval,
)
from pipeline.reports.ridership import compute_ridership_weighted_on_time_by_agency
from pipeline.reports.service_delivered import compute_service_delivered_by_agency, unreadable_service_delivered
from pipeline.reports.supply import compute_supply_metrics_by_agency
from pipeline.service_day import service_date_of

_log = logging.getLogger(__name__)

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


@async_lru_cache(maxsize=64, ttl_seconds=300)
async def compute_network_summary(conn, ch, from_date: date, to_date: date) -> list[dict[str, Any]]:
    """Per-agency rollups over [from_date, to_date]: the comparable agencies
    worst-avg-delay first, then the rest by name.

    The planned and delivered figures are one column of the board, not the
    board: a failure reading them, such as a schema behind its migrations,
    leaves every agency without a plan, and so unranked, as the freshness
    probe's degrade leaves it not stale, rather than failing the delay
    figures too.
    """
    agencies = await conn.fetch(
        "SELECT agency_id, agency_name, ingest_strategy FROM agencies WHERE deleted_at IS NULL ORDER BY agency_id"
    )
    perf = {r["agency_id"]: r for r in await conn.fetch(_PERF_SQL, from_date, to_date)}
    feed = {r["agency_id"]: r for r in await conn.fetch(_FEED_SQL, from_date, to_date)}
    agg_max = {r["agency_id"]: r["d"] for r in await conn.fetch(_AGG_MAX_SQL)}

    today_jst_midnight_utc = (
        datetime.now(_JST).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    )

    # One indexed read per agency (api.clickhouse.max_captured_at_before —
    # same index-served ORDER BY ... LIMIT 1 form as
    # pipeline.clickhouse.max_captured_at_before) instead of `maxOrNull`, which is a
    # full per-agency scan, materially slower than the indexed form.
    #
    # This probe backs ONLY the `is_stale` field below — every other field in
    # this function's result (avg_delay_min, on_time_pct, samples,
    # raw_samples, clamp_pct, data_from, data_to) comes from Postgres agg_*
    # tables. So a ClickHouse hiccup here must not fail the whole network
    # summary — degrade this agency's live_max to None instead (same
    # "one non-critical sub-check shouldn't sink an otherwise-fine response"
    # shape as api.routers.map.today_route_summary's freshness try/except).
    # is_stale(agg_day, None) is defined as "not stale" (see its docstring:
    # no completed day / can't determine → nothing owed), which is the
    # correct degrade here.
    # max_captured_at_before_by_agency runs the per-agency probes concurrently
    # and preserves the try/except degrade described above; shared with
    # pipeline.health.aggregate_freshness's identical shape.
    probed = await max_captured_at_before_by_agency(
        ch, [a["agency_id"] for a in agencies], today_jst_midnight_utc, _log
    )
    live_max: dict[int, "date | None"] = {
        aid: None if mx is None else service_date_of(mx) for aid, mx in probed.items()
    }

    # planned_trips reads the saved and loaded plans; executed_trips and
    # service_delivered_pct also read the precomputed non-executed aggregate
    # (agg_service_delivered_daily). No ClickHouse scan on this request path;
    # see compute_service_delivered_by_agency.
    coverage_through = max((perf[a["agency_id"]]["data_to"] for a in agencies if a["agency_id"] in perf), default=None)
    try:
        delivered = await compute_service_delivered_by_agency(
            conn, [a["agency_id"] for a in agencies], from_date, to_date, observed_through=coverage_through
        )
    except Exception:
        _log.warning("network summary: service-delivered figures unavailable", exc_info=True)
        unreadable = unreadable_service_delivered(from_date, to_date)
        delivered = {a["agency_id"]: unreadable for a in agencies}

    # static_version_id/planned_trip_count/planned_vehicle_km/
    # vehicle_km_delivered_pct: see pipeline.reports.supply — reuses
    # `delivered` (computed just above, over this SAME range) as the source
    # of the executed/planned trip ratio rather than recomputing it.
    supply = await compute_supply_metrics_by_agency(conn, [a["agency_id"] for a in agencies], delivered)

    # weighted_on_time_pct keyed by agency_id; an agency absent from this dict
    # (per compute_ridership_weighted_on_time_by_agency) has no
    # ridership_weights rows configured at all. Read via a sentinel default so
    # "absent" (not configured) and "present but None" (configured, but zero
    # samples in range) stay distinguishable below.
    weighted_on_time = await compute_ridership_weighted_on_time_by_agency(conn, from_date, to_date)
    _NOT_CONFIGURED = object()

    rows: list[dict[str, Any]] = []
    for a in agencies:
        aid = a["agency_id"]
        p = perf.get(aid)
        n = int(p["n"]) if p and p["n"] else 0
        avg_delay_min = round(float(p["sd"]) / n / 60, 1) if (p and n) else None
        on_time_count = int(p["ot"]) if p and p["ot"] else 0
        on_time_pct = round(on_time_count / n * 100, 1) if n else None
        f = feed.get(aid)
        raw = int(f["raw"]) if f and f["raw"] else 0
        clamp = int(f["clamp"]) if f and f["clamp"] else 0
        clamp_pct = round(clamp / raw * 100, 2) if raw else None
        data_from = p["data_from"].isoformat() if (p and p["data_from"]) else None
        data_to = p["data_to"].isoformat() if (p and p["data_to"]) else None
        d = delivered[aid]
        strategy = a["ingest_strategy"]
        # A count of trip_ids the timetable doesn't name is no count of its trips.
        matchable = strategy in COMPARABLE_INGEST_STRATEGIES
        observed_trips = d["observed_trips"] if matchable else None
        coverage_planned_trips = d["coverage_planned_trips"] if matchable else None
        coverage_pct = observation_coverage(strategy, observed_trips, coverage_planned_trips)
        ci_low, ci_high = on_time_interval(on_time_count, n, observed_trips)
        comparable, comparable_reason = assess(
            ingest_strategy=strategy,
            coverage_pct=coverage_pct,
            ci_low=ci_low,
            ci_high=ci_high,
            clamp_pct=clamp_pct,
            samples=n,
        )
        s = supply.get(
            aid,
            {
                "static_version_id": None,
                "planned_trip_count": None,
                "planned_vehicle_km": None,
                "vehicle_km_delivered_pct": None,
            },
        )
        agency_weighted = weighted_on_time.get(aid, _NOT_CONFIGURED)
        rows.append(
            {
                "agency_id": aid,
                "agency_name": a["agency_name"],
                "avg_delay_min": avg_delay_min,
                "on_time_pct": on_time_pct,
                "ci_low": ci_low,
                "ci_high": ci_high,
                "samples": n,
                "raw_samples": raw,
                "clamp_count": clamp,
                "clamp_pct": clamp_pct,
                "is_stale": is_stale(agg_max.get(aid), live_max.get(aid)),
                "data_from": data_from,
                "data_to": data_to,
                "planned_trips": d["planned_trips"],
                "executed_trips": d["executed_trips"],
                "service_delivered_pct": d["service_delivered_pct"],
                "covered_days": d["covered_days"],
                "window_days": d["window_days"],
                "delivered_missing": d["delivered_missing"],
                "observed_trips": observed_trips,
                "coverage_planned_trips": coverage_planned_trips,
                "coverage_through": coverage_through.isoformat() if coverage_through else None,
                "coverage_pct": coverage_pct,
                "comparable": comparable,
                "comparable_reason": comparable_reason,
                # has_ridership_weights distinguishes "not configured for
                # this agency" (weighted_on_time_pct is meaningless, hide any
                # toggle) from "configured, but None because there were no
                # samples in range" (weighted_on_time_pct itself already
                # says that).
                "has_ridership_weights": agency_weighted is not _NOT_CONFIGURED,
                "weighted_on_time_pct": None if agency_weighted is _NOT_CONFIGURED else agency_weighted,
                "static_version_id": s["static_version_id"],
                "planned_trip_count": s["planned_trip_count"],
                "planned_vehicle_km": s["planned_vehicle_km"],
                "vehicle_km_delivered_pct": s["vehicle_km_delivered_pct"],
            }
        )
    # Ranked: worst delay first, None (which a comparable row never has) last.
    # Ties keep agency_id order: `rows` is built 1:1 from `agencies`, which
    # the query orders by agency_id, and Python's sort() is stable.
    ranked = sorted(
        (r for r in rows if r["comparable"]),
        key=lambda r: (r["avg_delay_min"] is None, -(r["avg_delay_min"] or 0.0)),
    )
    # Unranked: by name, so no figure the gate refused implies an order.
    unranked = sorted((r for r in rows if not r["comparable"]), key=lambda r: (r["agency_name"], r["agency_id"]))
    return ranked + unranked


def invalidate_network_summary() -> None:
    """Forget every cached board, after a write to what it reads (an agency's
    row, or its ridership weights), so the board's next load shows the change
    instead of waiting out the cache's TTL."""
    compute_network_summary.cache_clear()  # type: ignore[attr-defined]
