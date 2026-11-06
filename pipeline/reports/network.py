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
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from api.clickhouse import max_captured_at_before_by_agency
from pipeline.cache import async_lru_cache
from pipeline.freshness import is_stale
from pipeline.reports.comparability import Assessment, assess
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

_AGENCIES_SQL = (
    "SELECT agency_id, agency_name, ingest_strategy FROM agencies WHERE deleted_at IS NULL ORDER BY agency_id"
)


def coverage_through(last_days: Iterable[date | None]) -> date | None:
    """Where observation coverage stops for every agency on the board: the
    newest of the agencies' last observed days in the range."""
    return max((d for d in last_days if d is not None), default=None)


@dataclass(frozen=True)
class AgencyFigures:
    """One agency's figures over a range as the board reads them: the whole
    agency, every day of the range, and the exact ``legacy_60s`` on-time
    count, whatever filter or tolerance a per-agency report applies to its
    own figures. All zero for an agency with no rows in range."""

    samples: int = 0
    sum_delay_sec: Decimal | int = 0
    on_time_count: int = 0
    data_from: date | None = None
    data_to: date | None = None
    raw_samples: int = 0
    clamp_count: int = 0

    @property
    def avg_delay_min(self) -> float | None:
        return round(float(self.sum_delay_sec) / self.samples / 60, 1) if self.samples else None

    @property
    def on_time_pct(self) -> float | None:
        return round(self.on_time_count / self.samples * 100, 1) if self.samples else None

    @property
    def clamp_pct(self) -> float | None:
        """The implausible-reading share; ``None`` without a feed-health row
        in range, which is not a measured 0%."""
        return round(self.clamp_count / self.raw_samples * 100, 2) if self.raw_samples else None


@dataclass(frozen=True)
class BoardFigures:
    """What the board reads for every agency on it over one range:
    ``agencies`` (the non-deleted ones, by agency_id, with each one's
    ingest strategy), ``figures`` by agency_id, and :func:`coverage_through`
    over all of them."""

    agencies: dict[int, Any]
    figures: dict[int, AgencyFigures]
    coverage_through: date | None

    def of(self, agency_id: int) -> AgencyFigures:
        return self.figures.get(agency_id, AgencyFigures())


async def read_board_figures(conn, from_date: date, to_date: date) -> BoardFigures:
    """The board's per-agency figures over ``[from_date, to_date]``, and
    where observation coverage stops for all of them. A single-agency report
    that judges its agency as the board does reads them here, so the bound
    is the board's, never its own agency's last day, which would excuse that
    agency's own missing trailing days."""
    agencies = {r["agency_id"]: r for r in await conn.fetch(_AGENCIES_SQL)}
    perf = {r["agency_id"]: r for r in await conn.fetch(_PERF_SQL, from_date, to_date)}
    feed = {r["agency_id"]: r for r in await conn.fetch(_FEED_SQL, from_date, to_date)}
    figures: dict[int, AgencyFigures] = {}
    for aid in perf.keys() | feed.keys():
        p, f = perf.get(aid), feed.get(aid)
        figures[aid] = AgencyFigures(
            samples=int(p["n"]) if p and p["n"] else 0,
            sum_delay_sec=p["sd"] if p and p["sd"] else 0,
            on_time_count=int(p["ot"]) if p and p["ot"] else 0,
            data_from=p["data_from"] if p else None,
            data_to=p["data_to"] if p else None,
            raw_samples=int(f["raw"]) if f and f["raw"] else 0,
            clamp_count=int(f["clamp"]) if f and f["clamp"] else 0,
        )
    through = coverage_through(figures[aid].data_to for aid in agencies if aid in figures)
    return BoardFigures(agencies=agencies, figures=figures, coverage_through=through)


def assess_agency(ingest_strategy: str | None, figures: AgencyFigures, delivered: dict[str, Any]) -> Assessment:
    """The comparability gate over one agency's board figures and its
    planned and observed trips
    (:func:`~pipeline.reports.service_delivered.compute_service_delivered_by_agency`,
    read with ``observed_through`` at :attr:`BoardFigures.coverage_through`)."""
    return assess(
        ingest_strategy=ingest_strategy,
        observed_trips=delivered["observed_trips"],
        coverage_planned_trips=delivered["coverage_planned_trips"],
        on_time_count=figures.on_time_count,
        samples=figures.samples,
        clamp_pct=figures.clamp_pct,
    )


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
    board = await read_board_figures(conn, from_date, to_date)
    agencies = list(board.agencies.values())
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
    # (agg_service_delivered_daily), and observed_trips agg_route_daily. No
    # ClickHouse scan on this request path; see
    # compute_service_delivered_by_agency.
    try:
        delivered = await compute_service_delivered_by_agency(
            conn, [a["agency_id"] for a in agencies], from_date, to_date, observed_through=board.coverage_through
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
        fig = board.of(aid)
        d = delivered[aid]
        gate = assess_agency(a["ingest_strategy"], fig, d)
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
                "avg_delay_min": fig.avg_delay_min,
                "on_time_pct": fig.on_time_pct,
                "ci_low": gate.ci_low,
                "ci_high": gate.ci_high,
                "samples": fig.samples,
                "raw_samples": fig.raw_samples,
                "clamp_count": fig.clamp_count,
                "clamp_pct": fig.clamp_pct,
                "is_stale": is_stale(agg_max.get(aid), live_max.get(aid)),
                "data_from": fig.data_from.isoformat() if fig.data_from else None,
                "data_to": fig.data_to.isoformat() if fig.data_to else None,
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


def invalidate_network_summary() -> None:
    """Forget every cached board, after a write to what it reads (an agency's
    row, or its ridership weights), so the board's next load shows the change
    instead of waiting out the cache's TTL."""
    compute_network_summary.cache_clear()  # type: ignore[attr-defined]
