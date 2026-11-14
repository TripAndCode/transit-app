"""Cross-agency network summary: per-agency rollups over precomputed aggregates.

Read-only. One GROUP BY agency_id read per source, merged on the full agencies
list. Range-scoped (date window only — whole-agency comparison, so service/
time_band/dow/routes are not applied). Reuses the pure freshness rule.

Only the agencies whose data supports a comparison are ranked:
`pipeline.reports.comparability.assess` gives ``comparable`` and the first
``comparable_reason`` it fails, with the figures it judged: ``coverage_pct``
(``observed_trips`` of ``coverage_planned_trips``), the on-time interval
``ci_low``/``ci_high`` and ``clamp_pct``. Comparable rows come first, worst
``avg_delay_min`` first; the rest follow in agency_id order, never by a figure
the data cannot support, and the board orders those by name. Coverage counts
the planned days up to :func:`coverage_through`, the newest day any agency has
observations for in the range: a later day is one the aggregates have not
reached for anyone yet, which is no one feed's missing trips, while a gap
inside that span counts against the feed that has it.

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
count only those, so a row whose ``covered_days`` falls short counts fewer
days than its neighbours and reads against them only with that note beside it.
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
from collections.abc import Iterable, Mapping
from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from api.clickhouse import newest_filed_date_before_by_agency
from pipeline.cache import async_lru_cache, cache_briefly
from pipeline.freshness import is_stale
from pipeline.histogram import on_time_count_sql
from pipeline.reports import comparability
from pipeline.reports.comparability import (
    BOARD_ON_TIME_WINDOW,
    MAX_CLAMP_PCT,
    MIN_COVERAGE_PCT,
    assess,
    clamp_is_high,
    coverage_is_low,
    coverage_unknown_why,
    matched_trips,
)
from pipeline.reports.definition import CoverageMissing, FeedQuality
from pipeline.reports.ridership import compute_ridership_weighted_on_time_by_agency
from pipeline.reports.savepoint import savepoint
from pipeline.reports.service_delivered import compute_service_delivered_by_agency, unreadable_service_delivered
from pipeline.reports.supply import compute_supply_metrics_by_agency

_log = logging.getLogger(__name__)

_JST = ZoneInfo("Asia/Tokyo")

# The board's on-time count, read off each row's exact columns
# (pipeline.histogram.on_time_count_sql), so no histogram is merged.
_PERF_SQL = f"""
    SELECT agency_id, SUM(samples) AS n, SUM(sum_delay_sec) AS sd,
           SUM({on_time_count_sql(*BOARD_ON_TIME_WINDOW)}) AS ot,
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


def coverage_through(last_days: Iterable[date | None]) -> date | None:
    """Where observation coverage stops for every agency on the board: the
    newest of the agencies' last observed days in the range."""
    return max((d for d in last_days if d is not None), default=None)


@async_lru_cache(maxsize=64, ttl_seconds=300)
async def compute_network_summary(conn, ch, from_date: date, to_date: date) -> list[dict[str, Any]]:
    """Per-agency rollups over [from_date, to_date]: the comparable agencies
    worst-avg-delay first, then the rest in agency_id order.

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

    # One indexed read per agency (api.clickhouse.newest_filed_date_before,
    # which ranges off the newest capture the sort index serves) instead of a
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
    # newest_filed_date_before_by_agency runs the per-agency probes concurrently
    # and preserves the try/except degrade described above; shared with
    # pipeline.health.aggregate_freshness's identical shape.
    live_max = await newest_filed_date_before_by_agency(
        ch, [a["agency_id"] for a in agencies], today_jst_midnight_utc, _log
    )

    through = coverage_through(perf[a["agency_id"]]["data_to"] for a in agencies if a["agency_id"] in perf)
    # planned_trips reads the saved and loaded plans; executed_trips and
    # service_delivered_pct also read the precomputed non-executed aggregate
    # (agg_service_delivered_daily), and observed_trips agg_route_daily. No
    # ClickHouse scan on this request path; see
    # compute_service_delivered_by_agency.
    try:
        delivered = await compute_service_delivered_by_agency(
            conn, [a["agency_id"] for a in agencies], from_date, to_date, observed_through=through
        )
    except Exception:
        _log.warning("network summary: service-delivered figures unavailable", exc_info=True)
        cache_briefly()
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
        clamp_pct = comparability.clamp_pct(clamp, raw)
        data_from = p["data_from"].isoformat() if (p and p["data_from"]) else None
        data_to = p["data_to"].isoformat() if (p and p["data_to"]) else None
        d = delivered[aid]
        gate = assess(
            ingest_strategy=a["ingest_strategy"],
            observed_trips=d["observed_trips"],
            coverage_planned_trips=d["coverage_planned_trips"],
            on_time_count=on_time_count,
            samples=n,
            clamp_pct=clamp_pct,
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
                "ci_low": gate.ci_low,
                "ci_high": gate.ci_high,
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
                "observed_trips": gate.observed_trips,
                "coverage_planned_trips": gate.coverage_planned_trips,
                "coverage_pct": gate.coverage_pct,
                "comparable": gate.comparable,
                "comparable_reason": gate.comparable_reason,
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
    # Ranked: worst delay first. Ties, and the unranked rows, keep agency_id
    # order: `rows` is built 1:1 from `agencies`, which the query orders by
    # agency_id, and Python's sort() is stable. The unranked rows are never
    # ordered by a figure the gate refused to compare; the board sorts them by name.
    ranked = sorted((r for r in rows if r["comparable"]), key=lambda r: -r["avg_delay_min"])
    return ranked + [r for r in rows if not r["comparable"]]


def feed_quality_of(agency_id: int, figures: Mapping[str, Any], *, observed: bool) -> FeedQuality:
    """The definition stamp's completeness and clamp share for *agency_id*
    from the figures its board row prints, which a council report's verdict
    carries too: ``coverage_pct`` of ``observed_trips`` and
    ``coverage_planned_trips`` as the gate gave them, the plan's
    ``planned_trips`` and ``delivered_missing``, and ``clamp_pct``.

    *observed* is whether the agency has any observation in the period, the
    gate's first check, so a plan with nothing observed reads ``no_data``
    rather than its 0%. Each flag is the gate's own check of the figure."""
    coverage = figures["coverage_pct"] if observed else None
    missing: CoverageMissing | None = None
    if not observed:
        missing = "no_data"
    elif coverage is None:
        missing = coverage_unknown_why(figures)
    clamp = figures["clamp_pct"]
    return FeedQuality(
        agency_id=agency_id,
        coverage_pct=coverage,
        observed_trips=None if coverage is None else figures["observed_trips"],
        coverage_planned_trips=None if coverage is None else figures["coverage_planned_trips"],
        coverage_missing=missing,
        coverage_low=coverage is not None and coverage_is_low(coverage),
        clamp_pct=clamp,
        clamp_missing=None if clamp is not None else "no_data",
        clamp_high=clamp is not None and clamp_is_high(clamp),
        min_coverage_pct=MIN_COVERAGE_PCT,
        max_clamp_pct=MAX_CLAMP_PCT,
    )


def board_feed_quality(rows: Iterable[Mapping[str, Any]]) -> list[FeedQuality]:
    """Each agency's :func:`feed_quality_of` from its row of
    :func:`compute_network_summary`, in agency_id order."""
    return [
        feed_quality_of(row["agency_id"], row, observed=row["samples"] > 0)
        for row in sorted(rows, key=lambda row: row["agency_id"])
    ]


# Every agency the board lists, and the one asked for whether listed or not:
# its ingest strategy and the newest day in the range it has observations for,
# one probe of agg_route_daily_dist's (agency_id, date) key each. The board's
# coverage stops at the newest of the listed agencies' days (coverage_through),
# and an agency without one has no observation in the range.
_AGENCY_DAYS_SQL = """
    SELECT a.agency_id, a.ingest_strategy, a.deleted_at IS NULL AS listed, newest.date AS newest
    FROM agencies a
    LEFT JOIN LATERAL (
        SELECT r.date FROM agg_route_daily_dist r
        WHERE r.agency_id = a.agency_id AND r.date BETWEEN $2 AND $3
        ORDER BY r.date DESC LIMIT 1
    ) newest ON true
    WHERE a.deleted_at IS NULL OR a.agency_id = $1
"""

# _FEED_SQL for one agency.
_AGENCY_FEED_SQL = """
    SELECT COALESCE(SUM(raw_samples), 0) AS raw, COALESCE(SUM(clamp_count), 0) AS clamp
    FROM agg_feed_health
    WHERE agency_id = $1 AND date BETWEEN $2 AND $3
"""


@async_lru_cache(maxsize=128, ttl_seconds=300, agency_arg="agency_id")
async def compute_agency_feed_quality(conn, agency_id: int, from_date: date, to_date: date) -> FeedQuality | None:
    """*agency_id*'s completeness and clamp share over [from_date, to_date],
    the figures its row of :func:`compute_network_summary` over the same range
    reads, without reading every agency's: its coverage stops at the newest
    day any listed agency has observations for, through the same
    :func:`~pipeline.reports.service_delivered.compute_service_delivered_by_agency`,
    and its clamp share sums the same ``agg_feed_health`` days. Postgres only.

    A failure reading the plan reads as ``unreadable``, as on the board. A
    failure reading the rest, such as a schema behind its migrations, gives
    None, which leaves the stamp without the figures rather than failing the
    report; so does an agency that does not exist."""
    try:
        async with savepoint(conn):
            agencies = await conn.fetch(_AGENCY_DAYS_SQL, agency_id, from_date, to_date)
            own = next((row for row in agencies if row["agency_id"] == agency_id), None)
            if own is None:
                return None
            raw, clamp = await conn.fetchrow(_AGENCY_FEED_SQL, agency_id, from_date, to_date)
    except Exception:
        _log.warning("feed quality: figures unavailable for agency %s", agency_id, exc_info=True)
        cache_briefly()
        return None
    observed = own["newest"] is not None
    figures: dict[str, Any] = {
        "coverage_pct": None,
        "observed_trips": None,
        "coverage_planned_trips": None,
        "planned_trips": 0,
        "delivered_missing": None,
        "clamp_pct": comparability.clamp_pct(int(clamp), int(raw)),
    }
    if observed:
        through = coverage_through(row["newest"] for row in agencies if row["listed"])
        try:
            async with savepoint(conn):
                delivered = await compute_service_delivered_by_agency(
                    conn, [agency_id], from_date, to_date, observed_through=through
                )
            d = delivered[agency_id]
        except Exception:
            _log.warning("feed quality: the plan is unavailable for agency %s", agency_id, exc_info=True)
            cache_briefly()
            d = unreadable_service_delivered(from_date, to_date)
        observed_trips, planned = matched_trips(
            own["ingest_strategy"], d["observed_trips"], d["coverage_planned_trips"]
        )
        figures.update(
            coverage_pct=comparability.coverage_pct(observed_trips, planned),
            observed_trips=observed_trips,
            coverage_planned_trips=planned,
            planned_trips=d["planned_trips"],
            delivered_missing=d["delivered_missing"],
        )
    return feed_quality_of(agency_id, figures, observed=observed)


def invalidate_network_summary() -> None:
    """Forget every cached board, and every agency's figures read the board's
    way, after a write to what they read (an agency's row, or its ridership
    weights), so the next load shows the change instead of waiting out the
    cache's TTL."""
    compute_network_summary.cache_clear()  # type: ignore[attr-defined]
    compute_agency_feed_quality.cache_clear()  # type: ignore[attr-defined]
