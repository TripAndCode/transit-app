"""Reports endpoints: queries scoped to the user's RangeCtx.

Each report is computed per request so the global time-range / DOW /
time-band filter changes the numbers. Most reports read precomputed ``agg_*``
tables and fall back to scanning ClickHouse ``updates`` only for a narrowed
time band (or, for on-time, a custom tolerance); others (dwell/run, forecast,
headway, performance) read aggregates only, and ``delay_certificate`` always
scans ClickHouse. ``rendered_at`` reflects the moment the request was served.
"""

import csv
import io
import logging
from datetime import date, datetime, timezone
from typing import Any, Literal

import asyncpg
from clickhouse_connect.driver.asyncclient import AsyncClient
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from api.deps import get_agency, get_agency_range_ctx, get_ch, get_conn, get_locale
from api.middleware.ratelimit import limiter, tier_limit
from api.range import RangeCtx, ctx_payload, get_range_ctx
from api.scope_applied import ALL_SIX, scope_applied
from pipeline.day_types import DayType
from pipeline.flags import aflag
from pipeline.query.formatter import (
    format_council_summary_footnotes,
    format_council_summary_text,
    format_delay_certificate_footnotes,
    format_delay_certificate_text,
    format_dwell_run_text,
    format_result,
    format_trend_text,
)
from pipeline.reports import (
    DEFAULT_DELAY_CERTIFICATE_THRESHOLD_SEC,
    DEFAULT_ROUTE_TABLE_SORT,
    MIN_GROUP_SAMPLES,
    ON_TIME_PRESETS,
    RANKING_MIN_SAMPLES,
    ROUTE_TABLE_SORTS,
    DefinitionMeta,
    compute_compare_ranking,
    compute_council_summary,
    compute_delay_certificate,
    compute_dow_ranking,
    compute_dwell_run_decomposition,
    compute_headway_quality_report,
    compute_hourly_heatmap,
    compute_on_time,
    compute_performance_standards,
    compute_rain_delay,
    compute_ranking,
    compute_route_table,
    compute_trend_series,
    compute_worst_5min,
    delay_certificate_observed,
    format_definition_csv_line,
    observation_disclaimer,
    resolve_definition_meta,
    simulation_disclaimer,
    sort_route_table,
)
from pipeline.reports.council import DELIVERED_HONOURS as COUNCIL_DELIVERED_HONOURS
from pipeline.reports.council import ON_TIME_HONOURS as COUNCIL_ON_TIME_HONOURS
from pipeline.reports.filters import _service_label
from pipeline.reports.forecast import (
    RECENT_DAYS,
    hourly_cells_to_dow_band,
    recent_window,
    summarize_agency_overview,
    summarize_expected_delay_heatmap,
)
from pipeline.reports.route_table import RouteTableOrder
from pipeline.reports.schedule_revision import get_schedule_revision_boundaries
from pipeline.reports.suggest import compute_suggestion
from pipeline.route_catalog import (
    RETIRED,
    ZERO_DELAY,
    left_out_counts,
    left_out_route_codes,
    listed_count,
    route_list,
    rows_to_fetch,
)
from pipeline.stats import annotate_on_time_pct_confidence, on_time_pct_low_confidence, on_time_pct_margin_pp
from pipeline.weather import attribution as weather_attribution

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/{agency_id}", tags=["reports"])

# Pooled average delay plus the number of observations that average rests on,
# for any aggregate carrying `sum_delay_sec`/`samples`. A row can carry a
# sample count with no delay sum behind it, so the numerator, the denominator
# AND the reported count are all FILTERed to the same row population: such a
# row must neither inflate the denominator nor be counted as evidence behind
# `avg_min`. One definition reused at every call site, so the two figures
# cannot drift apart.
_POOLED_DELAY_PROJECTION_SQL = (
    "(SUM(sum_delay_sec) FILTER (WHERE sum_delay_sec IS NOT NULL)::numeric "
    "    / NULLIF(SUM(samples) FILTER (WHERE sum_delay_sec IS NOT NULL), 0) / 60.0) AS avg_min, "
    "SUM(samples) FILTER (WHERE sum_delay_sec IS NOT NULL)::int AS samples "
)

# A published forecast older than this many days is dropped in favor of the
# seasonal baseline -- matches the parent spec's own freshness rule (the VPS
# workbench's daily predict job republishes well inside this window, so a
# stale row here means the job itself has stopped running, not a slow day).
_ML_FRESHNESS_DAYS = 2

# Mirrors ml.training.HORIZON_DAYS, duplicated (not imported) since this
# module runs in the production image, which never installs the optional
# `ml` group. Bounding service_date to this window (not just origin_date to
# recency) keeps each (route, weekday) slot to at most one row -- an older
# run's still origin-fresh leftover outside its own horizon would otherwise
# share a weekday with the new horizon's row for that slot.
_ML_HORIZON_DAYS = 7

# The rows the heatmap overlays: inside the horizon, from a recent publish, and
# within a day's 24 hours (no band covers a run's hour 24 or later). Only the
# latest publish (one generated_at) counts: a slot it dropped still holds a
# superseded model's row. Built from the constants above, never from input.
_ML_FRESH_ROWS = (
    "service_date > now()::date "
    f"AND service_date <= now()::date + {_ML_HORIZON_DAYS} "
    f"AND origin_date >= now()::date - {_ML_FRESHNESS_DAYS} "
    "AND hour < 24 "
    "AND generated_at = (SELECT MAX(generated_at) FROM agg_route_forecast_ml)"
)


# Each forecast row's own route-hour history, which the agency overview
# weights a band's forecast rows by. Columns are renamed so the shared
# predicate's bare names (hour, service_date, ...) mean only the forecast's.
_ML_PAST_JOIN = (
    "LEFT JOIN ("
    "  SELECT route_code AS past_route, dow AS past_dow, hour AS past_hour, "
    "    SUM(samples) FILTER (WHERE sum_delay_sec IS NOT NULL)::int AS past_samples, "
    "    SUM(sum_delay_sec) FILTER (WHERE sum_delay_sec IS NOT NULL) AS past_sum_delay_sec "
    "  FROM agg_route_hour_dow WHERE agency_id = $1 AND avg_min IS NOT NULL AND samples > 0 "
    "  GROUP BY route_code, dow, hour"
    ") past ON past.past_route = route_code AND past.past_dow = EXTRACT(ISODOW FROM service_date) "
    "  AND past.past_hour = hour "
)


async def _fetch_ml_cells(conn: asyncpg.Connection, agency_id: int, route_code: str | None) -> list[dict[str, Any]]:
    """Fresh `agg_route_forecast_ml` rows for this agency (and route, if
    given), as plain dicts with `dow`/`hour`/`mean_min`/`q10`/`q90`/
    `service_date`. Returns a list of
    rows rather than a (dow, hour)-keyed mapping: when `route_code` is
    `None` (the agency-wide overview), more than one route can share a
    (dow, hour) slot, and collapsing them into one dict entry per key would
    silently drop every route but the last one fetched.

    Only the agency-wide read (`route_code` `None`) also carries each row's
    own route-hour history from `agg_route_hour_dow`, which its bands weight
    the row by: `samples` (0 where that route-hour has none) and
    `sum_delay_sec` (null there). A single route's heatmap weights by its own
    baseline cells, so its read skips that join.

    Never raises: this overlay is additive and flag-gated, so a DB failure
    here (a migration not yet applied, a transient error) degrades to no
    overlay rather than 500ing a response that would otherwise have served
    a perfectly good baseline.
    """
    if not await aflag("ml_forecast"):
        return []
    agency_wide = route_code is None
    try:
        if agency_wide:
            rows = await conn.fetch(
                "SELECT service_date, hour, mean_min, q10, q90, past.past_samples, past.past_sum_delay_sec "
                f"FROM agg_route_forecast_ml {_ML_PAST_JOIN}WHERE agency_id = $1 AND {_ML_FRESH_ROWS}",
                agency_id,
            )
        else:
            rows = await conn.fetch(
                "SELECT service_date, hour, mean_min, q10, q90 FROM agg_route_forecast_ml "
                f"WHERE agency_id = $1 AND route_code = $2 AND {_ML_FRESH_ROWS}",
                agency_id,
                route_code,
            )
    except Exception:
        _log.warning("_fetch_ml_cells: could not read agg_route_forecast_ml -- degrading to no overlay", exc_info=True)
        return []
    cells: list[dict[str, Any]] = []
    for row in rows:
        cell = {
            "dow": row["service_date"].isoweekday(),
            "hour": row["hour"],
            "mean_min": row["mean_min"],
            "q10": row["q10"],
            "q90": row["q90"],
            "service_date": row["service_date"],
        }
        if agency_wide:
            cell["samples"] = row["past_samples"] or 0
            cell["sum_delay_sec"] = row["past_sum_delay_sec"]
        cells.append(cell)
    return cells


async def _fetch_span(conn: asyncpg.Connection, sql: str, *args: Any) -> tuple[date | None, date | None]:
    """`sql`'s `first`/`last` day. Never raises: a span only labels figures,
    so a failed read degrades to no dates rather than 500ing a response whose
    figures are fine."""
    try:
        row = await conn.fetchrow(sql, *args)
    except Exception:
        _log.warning("_fetch_span: could not read a forecast date span -- degrading to no dates", exc_info=True)
        return None, None
    return row["first"], row["last"]


async def _fetch_history_span(conn: asyncpg.Connection, agency_id: int) -> tuple[date | None, date | None]:
    """The days the overview's past averages pool. agg_route_hour_dow keeps no
    date; agg_hour_daily keeps the same rows it pools (those with a scheduled
    time), so its first and last day bound them."""
    return await _fetch_span(
        conn, "SELECT MIN(date) AS first, MAX(date) AS last FROM agg_hour_daily WHERE agency_id = $1", agency_id
    )


async def _fetch_route_span(
    conn: asyncpg.Connection, agency_id: int, route_code: str
) -> tuple[date | None, date | None]:
    """First and last analyzed day of one route, from agg_route_daily: no
    dated aggregate keeps exactly the rows agg_route_hour_dow pools per route,
    so this bounds them from the outside. route_code is not a prefix of that
    table's key, so the read walks the agency's index range until it finds
    the route; the caller skips it for a route with no pooled rows."""
    return await _fetch_span(
        conn,
        "SELECT MIN(date) AS first, MAX(date) AS last FROM agg_route_daily WHERE agency_id = $1 AND route_code = $2",
        agency_id,
        route_code,
    )


async def fetch_live_ml_agencies(conn: asyncpg.Connection) -> set[int]:
    """Agencies with any row `_fetch_ml_cells` would overlay while the
    `ml_forecast` flag is on; the flag check is the caller's. Never raises,
    for the same reason: a failed read reports no agency live."""
    try:
        rows = await conn.fetch(f"SELECT DISTINCT agency_id FROM agg_route_forecast_ml WHERE {_ML_FRESH_ROWS}")
    except Exception:
        _log.warning("fetch_live_ml_agencies: could not read agg_route_forecast_ml -- none live", exc_info=True)
        return set()
    return {row["agency_id"] for row in rows}


# Static metadata for the listing endpoint. Ordered for sidebar display.
_REPORT_TYPES = (
    "route_table",
    "ranking",
    "ranking_best",
    "on_time",
    "worst_5min",
    "trend",
    "compare_ranking",
    "dow_weekend",
    "dow_weekday",
    "dwell_run",
    "council_summary",
    "delay_certificate",
)

_RANKING_TYPES = frozenset({"ranking", "ranking_best"})
# The reports that leave out routes observed fewer than RANKING_MIN_SAMPLES
# times unless include_sparse asks for them.
_FLOORED_TYPES = _RANKING_TYPES | {"route_table"}
# The reports that list one row per route, route_code first.
_ROUTE_LIST_TYPES = frozenset(
    {
        "route_table",
        "ranking",
        "ranking_best",
        "on_time",
        "worst_5min",
        "compare_ranking",
        "dow_weekday",
        "dow_weekend",
    }
)
_ORDERED_TYPES = frozenset({"on_time", "route_table"})
_EARLY_TOLERANCE_TYPES = frozenset({"on_time", "route_table", "council_summary"})
_LATE_TOLERANCE_TYPES = frozenset({"on_time", "route_table", "worst_5min", "council_summary"})
# The scope's `late` is the on-time tolerance. worst_5min's late cutoff, and
# route_table's over-5-min share, keep their own over-5-min threshold, so the
# scope never moves them.
_SCOPE_LATE_TYPES = frozenset({"on_time", "route_table", "council_summary"})

# What each report's rows actually filter on. compare_ranking and the dow_*
# reports fix dow/service themselves; dwell_run cannot split by time band.
_REPORT_HONOURS: dict[str, tuple[str, ...]] = {
    "route_table": ALL_SIX,
    "ranking": ALL_SIX,
    "ranking_best": ALL_SIX,
    "on_time": ALL_SIX,
    "worst_5min": ALL_SIX,
    "trend": ALL_SIX,
    "compare_ranking": ("from", "to", "time_band", "routes"),
    "dow_weekend": ("from", "to", "time_band", "routes"),
    "dow_weekday": ("from", "to", "time_band", "routes"),
    "dwell_run": ("from", "to", "dow", "service", "routes"),
    "council_summary": COUNCIL_ON_TIME_HONOURS,
    "delay_certificate": ALL_SIX,
}


def report_scope_applied(report_type: str) -> dict[str, bool]:
    honoured = list(_REPORT_HONOURS[report_type])
    if report_type in _SCOPE_LATE_TYPES:
        honoured.append("late")
    if report_type in _EARLY_TOLERANCE_TYPES:
        honoured.append("early")
    return scope_applied(*honoured)


# What each panel endpoint filters on: headway_quality's aggregate carries
# neither service nor time band; performance_standards reads dates only; the
# forecast endpoints read all-time aggregates, the heatmap for the scope's one route.
_HEADWAY_SCOPE = scope_applied("from", "to", "dow", "routes")
_STANDARDS_SCOPE = scope_applied("from", "to")
_WEATHER_SCOPE = scope_applied("from", "to", "dow", "service", "routes")
_FORECAST_HEATMAP_SCOPE = scope_applied("routes")
_FORECAST_OVERVIEW_SCOPE = scope_applied()
_COUNCIL_DELIVERED_SCOPE = scope_applied(*COUNCIL_DELIVERED_HONOURS)


class ReportMeta(BaseModel):
    """Listing entry returned by ``GET /reports``."""

    report_type: str
    rendered_at: datetime


class ReportCtx(BaseModel):
    """Typed mirror of :func:`api.range.ctx_payload` — the range echo every
    endpoint returns, with the wire-level ``from``/``to`` key names."""

    from_: str = Field(alias="from")
    to: str
    dow: str
    time_band: str
    service: str = "all"
    routes: list[str] = []
    hour: str | None = None
    stop: str | None = None
    dir: int | None = None

    model_config = {"populate_by_name": True}


class ReportResponse(BaseModel):
    """Payload returned by ``GET /reports/{report_type}`` in JSON mode."""

    report_type: str
    rendered_at: datetime
    text: str
    rows: list
    ctx: ReportCtx
    # Which on-time/late tolerance (and the shared dedup/exclusion rule) this
    # response's rows actually used -- see pipeline.reports.definition. Always
    # present, even for report types with no tolerance concept (fields None),
    # so a caller comparing two responses can check the definition matches
    # instead of assuming it does.
    definition: DefinitionMeta
    scope_applied: dict[str, bool]
    # council_summary only (None elsewhere): the scope its planned/executed/
    # service-delivered figures and data-quality caveat honoured. `scope_applied`
    # is the scope of its on-time figures, which follow the whole filter.
    delivered_scope_applied: dict[str, bool] | None = None
    # How many routes (delay_certificate: trips) qualified before `limit` cut
    # the list (route_table, ranking, ranking_best, on_time, compare_ranking
    # and delay_certificate; None elsewhere), and, for route_table, ranking and
    # ranking_best only, the observation count below which a route's figures
    # are too thin to trust.
    rows_total: int | None = None
    reliable_min_samples: int | None = None
    # delay_certificate only (None elsewhere): whether the scope holds any
    # observed departure, so an empty list can say "no data" rather than
    # "nothing left late".
    observed: bool | None = None
    # Route lists only (None elsewhere): how many of the list's own rows are
    # retired route codes (pipeline.route_catalog.retired_route_codes), left
    # out by default and kept on include_retired. The same in every mode.
    # route_table counts them over every route it ranks, so the count stays
    # the same whatever its sort or limit.
    retired_rows: int | None = None
    # Route lists only (None elsewhere): how many of the list's own rows are
    # routes whose feed reports no delay at all
    # (pipeline.route_catalog.zero_delay_route_codes), left out by default and
    # kept on include_zero_delay. The same in every mode, and counted as
    # retired_rows is.
    zero_delay_rows: int | None = None
    # route_table only (None elsewhere): the sort and order its rows are in,
    # so a screen still showing this response while another sort loads can
    # label the rows it holds rather than the ones it asked for.
    sort: str | None = None
    order: str | None = None


def _report_ctx(ctx: RangeCtx) -> ReportCtx:
    """Typed wrapper over the shared echo so these responses keep a schema."""
    return ReportCtx.model_validate(ctx_payload(ctx))


@router.get("/reports", response_model=list[ReportMeta])
@limiter.limit(tier_limit)
async def list_reports(
    request: Request,
    agency_id: int = Depends(get_agency),
) -> list[dict[str, Any]]:
    """Static list of report types. ``rendered_at`` is request time."""
    now = datetime.now(timezone.utc)
    return [{"report_type": rt, "rendered_at": now} for rt in _REPORT_TYPES]


class HeadwayQualityRow(BaseModel):
    """One high-frequency route's pooled Excess Waiting Time / coefficient of
    variation / long-gap rate over the request's range, on the days of its
    ``day_type`` — see ``pipeline.reports.headway_quality.compute_headway_quality_report``.

    ``ewt_sec``/``cov``/``long_gap_rate`` are independently nullable, not
    nullable together: a route can appear here (it's classified
    high-frequency) yet still have no resolvable metric for the requested
    range slice (e.g. its scheduled mean-wait predates this feature and
    hasn't been re-analyzed). ``cov`` alone additionally requires at least
    two pooled samples, so a route pooled from a single-sample day can have
    a resolvable ``ewt_sec``/``long_gap_rate`` with a null ``cov``.
    """

    route_code: str
    ewt_sec: float | None
    cov: float | None
    long_gap_rate: float | None
    samples: int
    # The class of day pooled (the route's dominant timetable's), or None for every day.
    day_type: DayType | None = None


class HeadwayQualityResponse(BaseModel):
    """Payload for ``GET /headway_quality`` — a second, narrower metric
    panel restricted to high-frequency routes, meant to render alongside
    (not instead of) the ``on_time`` report for the same range."""

    rows: list[HeadwayQualityRow]
    # High-frequency routes with data in range, none of it on a day of their class.
    routes_off_timetable: int = 0
    ctx: ReportCtx
    scope_applied: dict[str, bool]


@router.get("/headway_quality", response_model=HeadwayQualityResponse)
@limiter.limit(tier_limit)
async def get_headway_quality(
    request: Request,
    agency_id: int = Depends(get_agency),
    conn: asyncpg.Connection = Depends(get_conn),
    ctx: RangeCtx = Depends(get_range_ctx),
) -> HeadwayQualityResponse:
    """Excess Waiting Time / CoV / long-gap rate, high-frequency routes only.

    Not part of the generic ``/reports/{report_type}`` dispatcher above
    (no CSV/definition-metadata concept applies to a route subset filtered
    server-side by classification, not by a ranking/threshold the caller
    chose) — a dedicated endpoint, like ``/forecast/overview`` below.
    """
    rows, off_timetable = await compute_headway_quality_report(agency_id, ctx, conn)
    return HeadwayQualityResponse(
        rows=[HeadwayQualityRow(**r) for r in rows],
        routes_off_timetable=off_timetable,
        ctx=_report_ctx(ctx),
        scope_applied=_HEADWAY_SCOPE,
    )


class PerformanceStandardRow(BaseModel):
    """One configured `route_performance_standards` row, joined
    against the current actual value of its `metric_type` and the resulting
    achievement rate / estimated bonus-or-deduction -- see
    `pipeline.reports.performance_standard.compute_performance_standards`
    for the exact formula.

    `actual_value`/`achievement_rate`/`estimated_bonus_deduction` are always
    `None` together: a route can be configured (it has a row here) yet still
    have no resolvable actual figure for the requested range (e.g. an
    `ewt_sec` standard on a route not classified high-frequency), or a
    `threshold_value` of exactly 0 makes the relative-deviation ratio
    undefined. `metric_scope` is `"agency"` for `vehicle_km_delivered_pct`
    (that figure has no per-route breakdown -- every route configured with
    it is compared against its own agency's rate) and `"route"` for
    `ewt_sec`; the frontend must label an `"agency"`-scoped row accordingly
    rather than implying a route-specific figure.
    """

    route_code: str
    metric_type: str
    metric_scope: str
    threshold_value: float
    bonus_malus_rate: float
    actual_value: float | None
    achievement_rate: float | None
    estimated_bonus_deduction: float | None


class PerformanceStandardsResponse(BaseModel):
    """Payload for `GET /performance_standards`. `disclaimer` is a
    server-rendered, already-localized caveat that MUST be surfaced
    verbatim wherever these figures are shown -- this is an internal
    simulation/estimate, never an actual invoice or contractual output."""

    rows: list[PerformanceStandardRow]
    ctx: ReportCtx
    disclaimer: str
    scope_applied: dict[str, bool]


@router.get("/performance_standards", response_model=PerformanceStandardsResponse)
@limiter.limit(tier_limit)
async def get_performance_standards(
    request: Request,
    agency_id: int = Depends(get_agency),
    conn: asyncpg.Connection = Depends(get_conn),
    ctx: RangeCtx = Depends(get_range_ctx),
    locale: str = Depends(get_locale),
) -> PerformanceStandardsResponse:
    """Per-route minimum-performance-standard achievement rate and
    estimated bonus/deduction -- an internal simulation only (see
    `PerformanceStandardsResponse.disclaimer`), never a real invoice.

    Like `/headway_quality`, a dedicated endpoint rather than a
    `/reports/{report_type}` dispatcher entry: this reads a manually
    populated config table joined against other reports' own compute
    functions, not a single `agg_*` table scan.
    """
    rows = await compute_performance_standards(agency_id, ctx, conn)
    return PerformanceStandardsResponse(
        rows=[PerformanceStandardRow(**r) for r in rows],
        ctx=_report_ctx(ctx),
        disclaimer=simulation_disclaimer(locale),
        scope_applied=_STANDARDS_SCOPE,
    )


class WeatherStation(BaseModel):
    """The one documented representative observation station an agency's
    weather comparison is keyed to (`agency_weather_stations`). `note` is the
    operator's own record of WHY this station represents this service area, so
    the figure can state what it is actually keyed to instead of implying
    service-area-wide weather. Like the rest of this response it is public --
    returned verbatim to unauthenticated callers -- which is what the column's
    contract states it is for; it never carries internal-only content."""

    station_id: str
    station_name: str
    note: str | None


class WeatherDelayGroup(BaseModel):
    """One side of the rain-vs-dry comparison.

    `avg_delay_sec` is pooled over every delay measurement on that side's days
    (exact raw-seconds sum over sample count), and is `None` exactly when the
    side has no days/samples. `avg_precip_mm` counts each matched day once,
    however many routes ran on it."""

    days: int
    samples: int
    avg_delay_sec: float | None
    avg_precip_mm: float | None


class WeatherDelayBucket(BaseModel):
    """One precipitation bucket (`pipeline.reports.weather.RAIN_BUCKET_LABELS`)
    of the additive breakdown alongside the wet/dry split -- same pooling rule
    as `WeatherDelayGroup.avg_delay_sec`, but bucketed by that day's own
    rainfall rather than the `wet_day_threshold_mm` cutoff."""

    label: str
    days: int
    samples: int
    avg_delay_sec: float | None


class WeatherDelayResponse(BaseModel):
    """Payload for `GET /weather_delay` -- observed rainfall matched to service
    days, NOT a forecast and NOT a causal claim; `disclaimer` says both in
    plain language and MUST be surfaced verbatim wherever these figures are.
    `attribution` carries the observation source AND the fact that the daily
    figures are this application's own aggregation of it, which the source's
    public-data terms require to travel with any derived figure.

    `available` is False when the agency has no representative station
    configured, or when no in-range service day could be matched to an
    observation -- callers render nothing at all in that case. `delta_sec`
    (rainy minus non-rainy, seconds) is `None` when either side has no days,
    e.g. a window with no rainy days, which is a real answer rather than
    missing data; `low_confidence` is set whenever either side is thin enough
    that the difference should not be read as a stable effect. `buckets` is a
    finer, additive breakdown of the same matched days and does not affect
    `wet`/`dry`/`delta_sec`/`low_confidence`.
    """

    available: bool
    station: WeatherStation | None
    wet_day_threshold_mm: float
    wet: WeatherDelayGroup
    dry: WeatherDelayGroup
    delta_sec: float | None
    low_confidence: bool
    buckets: list[WeatherDelayBucket]
    ctx: ReportCtx
    disclaimer: str
    attribution: str
    scope_applied: dict[str, bool]


@router.get("/weather_delay", response_model=WeatherDelayResponse)
@limiter.limit(tier_limit)
async def get_weather_delay(
    request: Request,
    agency_id: int = Depends(get_agency),
    ctx: RangeCtx = Depends(get_agency_range_ctx),
    conn: asyncpg.Connection = Depends(get_conn),
    locale: str = Depends(get_locale),
) -> WeatherDelayResponse:
    """Average delay on observed-rainy service days vs non-rainy ones.

    Like `/headway_quality` and `/performance_standards`, a dedicated endpoint
    rather than a `/reports/{report_type}` dispatcher entry: it joins an
    external observation table to the daily aggregates and answers with a
    two-group comparison, not a route ranking with tolerance/CSV semantics.
    """
    result = await compute_rain_delay(agency_id, ctx, conn)
    return WeatherDelayResponse(
        **result,
        ctx=_report_ctx(ctx),
        disclaimer=observation_disclaimer(locale),
        attribution=weather_attribution(locale),
        scope_applied=_WEATHER_SCOPE,
    )


class SuggestionResponse(BaseModel):
    """Payload for GET /reports/suggest -- the Insight Panel's single pick."""

    report_type: str
    route_code: str
    reason_text: str
    severity: str
    # The evaluation window the rule that produced this suggestion actually
    # used (1 day for the anomaly rule, 7 days for trend-shift/on-time) --
    # ISO date strings so the frontend can pin its click-through navigation
    # to the window the reason text describes, instead of the user's ambient
    # (possibly 30-day) Analysis tab filter, which can dilute or hide the
    # very signal being described.
    from_date: str
    to_date: str


class SuggestionEnvelope(BaseModel):
    """Payload for GET /reports/suggest.

    ``suggestion`` is ``null`` when no rule produced a pick. The envelope
    exists so that "no signal" stays a described 200 body a client can read
    fields off, rather than a bare ``null`` with nowhere to say why.
    """

    suggestion: SuggestionResponse | None = None


@router.get("/reports/suggest", response_model=SuggestionEnvelope)
@limiter.limit(tier_limit)
async def get_suggestion(
    request: Request,
    agency_id: int = Depends(get_agency),
    conn: asyncpg.Connection = Depends(get_conn),
    ch: AsyncClient = Depends(get_ch),
    locale: str = Depends(get_locale),
    exclude: list[str] = Query(default=[]),
) -> SuggestionEnvelope:
    """One rule-based 'go look at this' suggestion for the Analysis tab's
    Insight Panel. ``exclude`` entries are ``"report_type:route_code"``
    pairs the frontend has already shown this session (sessionStorage-backed,
    stateless here). Returns ``{"suggestion": null}`` when every rule's
    candidates are excluded or the agency has no data at all -- the frontend
    renders its own calm 'no signal' copy for that case, not this endpoint.
    """
    exclude_set: frozenset[tuple[str, str]] = frozenset(
        (report_type, route_code) for item in exclude if ":" in item for report_type, route_code in [item.split(":", 1)]
    )
    result = await compute_suggestion(agency_id, conn, ch, exclude=exclude_set, locale=locale)
    return SuggestionEnvelope(suggestion=SuggestionResponse(**result) if result else None)


class ForecastHeatmapCell(BaseModel):
    """One day-of-week × hour cell of the forecast heatmap."""

    dow: int
    hour: int
    expected_avg_min: float | None
    samples: int
    low_confidence: bool
    source: Literal["ml", "baseline"]
    # The forecast's 10th-90th percentile range, each bound the mean over the
    # slot's trips; null on a cell that shows a past average.
    q10: float | None = None
    q90: float | None = None


class ForecastDay(BaseModel):
    """A weekday row whose cells come from the forecast model, and the
    service date that model run forecasts for it."""

    dow: int
    service_date: date


class ForecastHeatmapResponse(BaseModel):
    """Payload for ``GET /forecast/heatmap`` — the full 7×24 day×hour grid.
    `forecast_days` names the dates this route's ML cells forecast, oldest
    first; `history_from`/`history_to` bound the analyzed days of this
    route that its past averages pool (null for a route with none)."""

    route: str
    cells: list[ForecastHeatmapCell]
    forecast_days: list[ForecastDay]
    history_from: date | None
    history_to: date | None
    disclaimer: str
    scope_applied: dict[str, bool]


@router.get("/forecast/heatmap", response_model=ForecastHeatmapResponse)
@limiter.limit(tier_limit)
async def forecast_heatmap(
    request: Request,
    route: str = Query(..., min_length=1),
    agency_id: int = Depends(get_agency),
    conn: asyncpg.Connection = Depends(get_conn),
    locale: str = Depends(get_locale),
) -> dict[str, Any]:
    """Expected delay by day-of-week (ISODOW 1=Mon..7=Sun) × hour (0..23) for a
    route, pooled across service types (sample-weighted = exact pooled mean).
    Seasonal-naive baseline, NOT a prediction; carries a disclaimer.
    """
    rows = await conn.fetch(
        "SELECT dow, hour, " + _POOLED_DELAY_PROJECTION_SQL + "FROM agg_route_hour_dow "
        "WHERE agency_id = $1 AND route_code = $2 AND avg_min IS NOT NULL AND samples > 0 "
        "GROUP BY dow, hour ORDER BY dow, hour",
        agency_id,
        route,
    )
    ml_cells = await _fetch_ml_cells(conn, agency_id, route)
    history_from, history_to = await _fetch_route_span(conn, agency_id, route) if rows else (None, None)
    return {
        **summarize_expected_delay_heatmap(rows, route, locale, ml_cells=ml_cells),
        "history_from": history_from,
        "history_to": history_to,
        "scope_applied": _FORECAST_HEATMAP_SCOPE,
    }


class ForecastOverviewGridCell(BaseModel):
    """One day-of-week × time-band cell of the agency overview grid."""

    dow: int
    band: str
    expected_avg_min: float | None
    samples: int
    low_confidence: bool
    source: Literal["ml", "baseline"]


class ForecastOverviewWorst(BaseModel):
    """The single worst (highest pooled delay) window agency-wide. `source` is
    its band's: an "ml" figure is a forecast model's point, not an average."""

    dow: int
    band: str
    expected_avg_min: float
    samples: int
    source: Literal["ml", "baseline"]


class ForecastOverviewRoute(BaseModel):
    """One route in the delay-ranked list."""

    route_code: str
    route_name: str
    expected_avg_min: float
    samples: int
    low_confidence: bool
    # The last RECENT_DAYS analyzed calendar days' average delay for this
    # route, oldest first (from agg_route_daily — a different, seasonally-pooled source
    # than expected_avg_min above). Empty when the route has no recent
    # agg_route_daily rows (e.g. it hasn't run in the last week).
    recent_daily: list[float] = []


class ForecastOverviewResponse(BaseModel):
    """Payload for ``GET /forecast/overview`` — agency-wide landing view.
    `forecast_days` names the dates the grid's ML cells forecast, oldest
    first. `history_from`/`history_to` bound the analyzed days the past
    averages pool (the baseline cells and every route's figure), and
    `recent_from`/`recent_to` the window `recent_daily` draws from. Each
    pair is null for an agency with no analyzed day."""

    grid: list[ForecastOverviewGridCell]
    worst: ForecastOverviewWorst | None
    routes: list[ForecastOverviewRoute]
    forecast_days: list[ForecastDay]
    history_from: date | None
    history_to: date | None
    recent_from: date | None
    recent_to: date | None
    disclaimer: str
    scope_applied: dict[str, bool]


async def _fetch_recent_daily_rows(
    conn: asyncpg.Connection, agency_id: int
) -> tuple[list[asyncpg.Record], date | None, date | None]:
    """The last RECENT_DAYS analyzed calendar days per route, from
    agg_route_daily (real per-date rows) — a different table than route_rows
    in forecast_overview (which pools ALL time from the seasonal
    agg_route_hour_dow) — with the first and last day of that window. Powers
    each route's sparkline. NULL MAX(date) (brand-new agency, no agg rows yet)
    makes the WHERE clause's date comparisons false, so this safely returns
    zero rows and no window rather than erroring.
    """
    rows = await conn.fetch(
        "WITH span AS ("
        "  SELECT MIN(date) AS first, MAX(date) AS last FROM agg_route_daily WHERE agency_id = $1"
        ") "
        # sum_delay_sec is nullable (unlike samples); FILTER both sides to the
        # same row population — see forecast_heatmap's identical rationale.
        "SELECT d.date, d.route_code, span.first AS span_first, span.last AS span_last, "
        "  (SUM(d.sum_delay_sec) FILTER (WHERE d.sum_delay_sec IS NOT NULL)::numeric "
        "      / NULLIF(SUM(d.samples) FILTER (WHERE d.sum_delay_sec IS NOT NULL), 0) / 60.0) AS avg_min "
        "FROM agg_route_daily d, span "
        f"WHERE d.agency_id = $1 AND d.date > span.last - {RECENT_DAYS} AND d.date <= span.last "
        "GROUP BY d.date, d.route_code, span.first, span.last "
        "ORDER BY d.route_code, d.date",
        agency_id,
    )
    if not rows:
        return [], None, None
    return rows, *recent_window(rows[0]["span_first"], rows[0]["span_last"])


@router.get("/forecast/overview", response_model=ForecastOverviewResponse)
@limiter.limit(tier_limit)
async def forecast_overview(
    request: Request,
    agency_id: int = Depends(get_agency),
    conn: asyncpg.Connection = Depends(get_conn),
    locale: str = Depends(get_locale),
) -> dict[str, Any]:
    """Agency-wide expected delay: a 7-day × time-band grid (pooled across all
    routes), the worst window, and a delay-ranked route list. Seasonal-naive
    baseline, NOT a prediction; carries a disclaimer. Re-pools agg_route_hour_dow
    (no dedicated aggregate — the table is small enough to pool on read).
    """
    grid_rows = await conn.fetch(
        "SELECT dow, hour, " + _POOLED_DELAY_PROJECTION_SQL + "FROM agg_route_hour_dow "
        "WHERE agency_id = $1 AND avg_min IS NOT NULL AND samples > 0 "
        "GROUP BY dow, hour",
        agency_id,
    )
    route_rows = await conn.fetch(
        "WITH ra AS ("
        "  SELECT route_code, " + _POOLED_DELAY_PROJECTION_SQL + "  FROM agg_route_hour_dow "
        "  WHERE agency_id = $1 AND avg_min IS NOT NULL AND samples > 0 "
        "  GROUP BY route_code"
        "), labels AS ("
        "  SELECT DISTINCT ON (route_code) route_code, route_short_name, route_long_name FROM ("
        "    SELECT regexp_replace(route_id, '.*\\((\\d+)\\)$', '\\1') AS route_code, "
        "           route_short_name, route_long_name "
        "    FROM static_routes WHERE agency_id = $1"
        "  ) s ORDER BY route_code, route_short_name"
        ") "
        "SELECT ra.route_code, ra.avg_min, ra.samples, "
        "  COALESCE(NULLIF(l.route_short_name, ''), NULLIF(l.route_long_name, ''), ra.route_code) AS route_name "
        "FROM ra LEFT JOIN labels l USING (route_code)",
        agency_id,
    )
    # Purely decorative (unlike grid_rows/route_rows above), so a failure here
    # degrades to no sparklines, and so no sparkline window, instead of
    # 500ing the whole response.
    recent_from: date | None
    recent_to: date | None
    try:
        recent_daily_rows, recent_from, recent_to = await _fetch_recent_daily_rows(conn, agency_id)
    except Exception:
        _log.warning(
            "forecast_overview: recent-daily sparkline fetch failed for agency %s — degrading to no sparklines",
            agency_id,
            exc_info=True,
        )
        recent_daily_rows, recent_from, recent_to = [], None, None
    ml_cells = await _fetch_ml_cells(conn, agency_id, None)
    history_from, history_to = await _fetch_history_span(conn, agency_id)
    return {
        **summarize_agency_overview(grid_rows, route_rows, recent_daily_rows, locale, ml_cells=ml_cells),
        "history_from": history_from,
        "history_to": history_to,
        "recent_from": recent_from,
        "recent_to": recent_to,
        "scope_applied": _FORECAST_OVERVIEW_SCOPE,
    }


# Marker for an "on_time" or "route_table" row's `low_confidence` flag in the
# CSV export: the caveat mark ReportTable.tsx's on-time column and
# frontend/src/tabs/ask/RichResult.tsx show for the same signal (a short mark
# when true, blank when false), never the raw Python bool's str() form. The
# flag measures the percentage's interval, not its observation count, and the
# mark says so.
_LOW_CONFIDENCE_CSV_MARK = "誤差大"

# Column headers used when emitting CSV. Japanese labels for operator-facing
# downloads. Must match the row tuple shape produced by each compute_*.
_REPORT_CSV_COLUMNS: dict[str, list[str]] = {
    "route_table": [
        "系統コード",
        "平均遅延(分)",
        "定時率(%)",
        "5分超の割合(%)",
        "5分超回数",
        "観測数",
        "誤差大",
        "誤差(±ポイント)",
    ],
    "ranking": ["系統コード", "種別", "平均遅延(分)", "中央値(分)", "p90(分)", "観測数"],
    "ranking_best": ["系統コード", "種別", "平均遅延(分)", "中央値(分)", "p90(分)", "観測数"],
    "on_time": ["系統コード", "種別", "定時率(%)", "平均遅延(分)", "観測数", "誤差大", "誤差(±ポイント)"],
    "worst_5min": ["系統コード", "種別", "5分超回数", "平均遅延(分)", "観測数"],
    "compare_ranking": ["系統コード", "平日(分)", "土日祝(分)", "差(絶対値)", "差(符号付き)"],
    "dow_weekend": ["系統コード", "種別", "日種別", "平均遅延(分)", "観測数"],
    "dow_weekday": ["系統コード", "種別", "日種別", "平均遅延(分)", "観測数"],
    "trend": ["日付", "平均遅延(分)", "7日移動平均(分)", "観測数", "悪化系統トップ3"],
    "dwell_run": ["系統コード", "種別", "滞留観測数", "滞留平均(秒)", "走行観測数", "走行平均(秒)"],
    "council_summary": ["定時率(%)", "平均遅延(分)", "観測数", "計画本数", "運行本数", "運行実績率(%)"],
    "delay_certificate": ["事業者名", "系統コード", "種別", "日付", "定刻", "実績時刻", "遅延(秒)"],
}

# A downloaded file's name, where the report's user-facing name differs from
# its type identifier: the delay_certificate type is shown to users as a
# delay record, which is not an official certificate.
_CSV_FILE_STEMS: dict[str, str] = {"delay_certificate": "delay_record"}


def _csv_response(
    report_type: str,
    rows: list,
    ctx: RangeCtx,
    definition: DefinitionMeta,
    *,
    unavailable_message: str | None = None,
    extra_footnotes: list[str] | None = None,
) -> StreamingResponse:
    """Stream a UTF-8 BOM CSV (BOM lets Excel auto-detect Japanese encoding).

    The first data row (before the column header) is a single-cell
    definition-metadata preamble (see
    ``pipeline.reports.definition.format_definition_csv_line``) so a CSV
    exported with non-default tolerances shows those exact values instead of
    silently reading as the legacy_60s default. `extra_footnotes`, when
    given, adds one single-cell preamble row per string AFTER that line and
    BEFORE the column header -- the council_summary report type's
    freshness/quality caveats (see
    ``pipeline.query.formatter.format_council_summary_footnotes``), kept
    Japanese-only here (like the definition-metadata line itself) rather
    than locale-switched, matching this CSV export's existing operator-facing
    convention.

    `unavailable_message`, when given, replaces the (otherwise empty) data
    rows with a single explanatory row instead -- for a report type whose
    JSON/text rendering already distinguishes "genuinely zero observations"
    from "this agency/filter can't produce this report at all"
    (`dwell_run`'s `available`/`time_band_supported` flags), an empty CSV
    with only a header row would silently collapse that same distinction
    back into a misleading blank/zero.
    """
    cols = _REPORT_CSV_COLUMNS.get(report_type, [])
    buf = io.StringIO()
    buf.write("﻿")  # BOM
    w = csv.writer(buf)
    w.writerow([format_definition_csv_line(definition)])
    for line in extra_footnotes or []:
        w.writerow([line])
    w.writerow(cols)
    if unavailable_message is not None:
        w.writerow([unavailable_message])
    elif report_type == "trend":
        for d in rows:
            offenders = "; ".join(o.get("route_code", "") for o in (d.get("top_offenders") or []))
            w.writerow([d.get("date"), d.get("avg_min"), d.get("avg_min_smoothed"), d.get("samples"), offenders])
    elif report_type in ("on_time", "route_table"):
        for r in rows:
            *lead, low_confidence, margin_pp = r
            w.writerow([*lead, _LOW_CONFIDENCE_CSV_MARK if low_confidence else "", margin_pp])
    elif report_type == "dwell_run":
        for r in rows:
            w.writerow(
                [
                    r.get("route_code"),
                    r.get("service_type") or "",
                    r.get("dwell_samples"),
                    r.get("dwell_avg_sec"),
                    r.get("run_samples"),
                    r.get("run_avg_sec"),
                ]
            )
    else:
        for r in rows:
            w.writerow(list(r))
    buf.seek(0)
    fname = f"{_CSV_FILE_STEMS.get(report_type, report_type)}_{ctx.from_date}_{ctx.to_date}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.get("/reports/{report_type}", response_model=ReportResponse)
@limiter.limit(tier_limit)
async def get_report(
    request: Request,
    report_type: str,
    limit: int | None = Query(default=None, ge=1, le=500),
    format: str | None = Query(default=None, pattern="^(json|csv)$"),
    preset: str | None = Query(
        default=None,
        description="Named on-time/late tolerance preset (currently only 'legacy_60s'). "
        "Mutually exclusive with early_tolerance_sec/late_tolerance_sec.",
    ),
    early_tolerance_sec: int | None = Query(
        default=None,
        ge=0,
        description="on_time/council_summary only: how many seconds early a departure may still be "
        "'on time'. Unset means unbounded (any early departure counts), matching legacy_60s.",
    ),
    late_tolerance_sec: int | None = Query(
        default=None,
        ge=0,
        description="on_time/worst_5min/council_summary: the late-side cutoff (default 60s for "
        "on_time/council_summary, 300s for worst_5min). Passing this opts into a query-time "
        "histogram estimate instead of the exact legacy_60s column.",
    ),
    late: int | None = Query(
        default=None,
        ge=0,
        le=3600,
        description="The scope's on-time late tolerance in seconds. Applied by on_time and "
        "council_summary; otherwise ignored and reported false in scope_applied (worst_5min's "
        "threshold stays its own late_tolerance_sec).",
    ),
    early: int | None = Query(
        default=None,
        ge=0,
        le=3600,
        description="The scope's early tolerance in seconds. Applied by on_time and council_summary; "
        "otherwise ignored and reported false in scope_applied.",
    ),
    threshold_sec: int | None = Query(
        default=None,
        ge=0,
        description="delay_certificate only: a departure's dep_delay must STRICTLY EXCEED this "
        "many seconds to be included. Defaults to "
        "pipeline.reports.council.DEFAULT_DELAY_CERTIFICATE_THRESHOLD_SEC.",
    ),
    include_sparse: bool = Query(
        default=False,
        description="route_table/ranking/ranking_best only: also rank routes observed fewer than "
        "reliable_min_samples times in the period.",
    ),
    include_retired: bool = Query(
        default=False,
        description="Route lists only: keep route codes the current timetable no longer runs "
        "(see pipeline.route_catalog.retired_route_codes), which are left out by default.",
    ),
    include_zero_delay: bool = Query(
        default=False,
        description="Route lists only: keep routes whose every observation reads exactly 0 seconds of "
        "delay (see pipeline.route_catalog.zero_delay_route_codes), which are left out by default.",
    ),
    order: str | None = Query(
        default=None,
        pattern="^(worst|best)$",
        description="on_time and route_table only: 'worst' (the default) lists the routes that need "
        "attention first (on_time: the lowest on-time rate); 'best' the other end first.",
    ),
    sort: str | None = Query(
        default=None,
        pattern=f"^({'|'.join(ROUTE_TABLE_SORTS)})$",
        description="route_table only: the figure the table is sorted by, the average delay "
        f"('{DEFAULT_ROUTE_TABLE_SORT}') unless named.",
    ),
    agency_id: int = Depends(get_agency),
    ctx: RangeCtx = Depends(get_agency_range_ctx),
    conn: asyncpg.Connection = Depends(get_conn),
    ch: AsyncClient = Depends(get_ch),
    locale: str = Depends(get_locale),
) -> ReportResponse | StreamingResponse:
    """Compute the named report live and render it."""
    if report_type not in _REPORT_TYPES:
        raise HTTPException(status_code=404, detail=f"Unknown report type '{report_type}'")

    if late is not None and late_tolerance_sec is not None:
        raise HTTPException(status_code=400, detail="late and late_tolerance_sec set the same tolerance; pass one")
    if early is not None and early_tolerance_sec is not None:
        raise HTTPException(status_code=400, detail="early and early_tolerance_sec set the same tolerance; pass one")
    if late is not None and report_type in _SCOPE_LATE_TYPES:
        late_tolerance_sec = late
    if early is not None and report_type in _EARLY_TOLERANCE_TYPES:
        early_tolerance_sec = early

    if preset is not None:
        if early_tolerance_sec is not None or late_tolerance_sec is not None:
            raise HTTPException(
                status_code=400, detail="preset cannot be combined with early_tolerance_sec/late_tolerance_sec"
            )
        if preset not in ON_TIME_PRESETS:
            raise HTTPException(status_code=400, detail=f"Unknown preset '{preset}'")
        early_tolerance_sec, late_tolerance_sec = ON_TIME_PRESETS[preset]
    if early_tolerance_sec is not None and report_type not in _EARLY_TOLERANCE_TYPES:
        raise HTTPException(
            status_code=400,
            detail="early_tolerance_sec only applies to the on_time/route_table/council_summary reports",
        )
    if late_tolerance_sec is not None and report_type not in _LATE_TOLERANCE_TYPES:
        raise HTTPException(
            status_code=400,
            detail="late_tolerance_sec only applies to the on_time/route_table/worst_5min/council_summary reports",
        )
    if threshold_sec is not None and report_type != "delay_certificate":
        raise HTTPException(status_code=400, detail="threshold_sec only applies to the delay_certificate report")
    if include_sparse and report_type not in _FLOORED_TYPES:
        raise HTTPException(
            status_code=400, detail="include_sparse only applies to the ranking and route_table reports"
        )
    if include_retired and report_type not in _ROUTE_LIST_TYPES:
        raise HTTPException(status_code=400, detail="include_retired only applies to the route lists")
    if include_zero_delay and report_type not in _ROUTE_LIST_TYPES:
        raise HTTPException(status_code=400, detail="include_zero_delay only applies to the route lists")
    if order is not None and report_type not in _ORDERED_TYPES:
        raise HTTPException(status_code=400, detail="order only applies to the on_time and route_table reports")
    if sort is not None and report_type != "route_table":
        raise HTTPException(status_code=400, detail="sort only applies to the route_table report")

    # Resolved from the same (now-validated) params compute_on_time/
    # compute_worst_5min themselves consume below, so this can never show a
    # tolerance different from the one the rows were actually computed with.
    definition = resolve_definition_meta(report_type, early_tolerance_sec, late_tolerance_sec)

    n = limit or 100
    intent: dict = {}
    rows: list
    rows_total: int | None = None
    left_out: dict[str, frozenset[str]] = {}
    if report_type in _ROUTE_LIST_TYPES:
        left_out = await left_out_route_codes(conn, agency_id, ctx)
    kept = {reason for reason, keep in ((RETIRED, include_retired), (ZERO_DELAY, include_zero_delay)) if keep}
    n_fetch = rows_to_fetch(n, left_out)
    left_out_rows = dict.fromkeys(left_out, 0)
    table_sort: str | None = None
    table_order: RouteTableOrder | None = None

    def listed(route_rows: list) -> list:
        """pipeline.route_catalog.route_list over this request, recording each
        reason's count for the response."""
        shown, counts = route_list(route_rows, n, left_out, kept)
        left_out_rows.update(counts)
        return shown

    def listed_total(route_rows: list) -> int:
        return listed_count(route_rows, left_out, kept)

    if report_type == "route_table":
        table_sort = sort or DEFAULT_ROUTE_TABLE_SORT
        table_order = "best" if order == "best" else "worst"
        table = sort_route_table(
            await compute_route_table(
                agency_id,
                ctx,
                conn,
                ch=ch,
                min_samples=MIN_GROUP_SAMPLES if include_sparse else RANKING_MIN_SAMPLES,
                early_tolerance_sec=early_tolerance_sec,
                late_tolerance_sec=late_tolerance_sec,
            ),
            table_sort,
            table_order,
        )
        rows_total = listed_total(table)
        rows = [(*r, on_time_pct_low_confidence(r[2], r[5]), on_time_pct_margin_pp(r[2], r[5])) for r in listed(table)]
        left_out_rows.update(left_out_counts(table, left_out))
        intent = {"query_type": "route_table", "sort": table_sort, "order": table_order, "service": _service_label(ctx)}
    elif report_type in _RANKING_TYPES:
        sort_order = "asc" if report_type == "ranking_best" else "desc"
        ranked = await compute_ranking(
            agency_id,
            ctx,
            conn,
            ch=ch,
            sort_order=sort_order,
            limit=None,
            min_samples=MIN_GROUP_SAMPLES if include_sparse else RANKING_MIN_SAMPLES,
        )
        rows_total = listed_total(ranked)
        rows = listed(ranked)
        intent = {"query_type": "ranking", "sort_order": sort_order, "service": _service_label(ctx)}
    elif report_type == "on_time":
        sort_order = "desc" if order == "best" else "asc"
        rated = await compute_on_time(
            agency_id,
            ctx,
            conn,
            ch=ch,
            limit=None,
            sort_order=sort_order,
            early_tolerance_sec=early_tolerance_sec,
            late_tolerance_sec=late_tolerance_sec,
        )
        rows_total = listed_total(rated)
        # Appends `low_confidence` (95% Wilson interval too wide to trust the
        # percentage) and `margin_pp` (that interval's half-width) as a
        # display-layer annotation — doesn't change compute_on_time's own
        # 5-tuple contract, so pooling callers (pipeline.reports.suggest) and
        # the Ask tools' columns are unaffected. See pipeline/stats.py.
        rows = [(*r, on_time_pct_margin_pp(r[2], r[4])) for r in annotate_on_time_pct_confidence(listed(rated))]
        intent = {"query_type": "on_time", "sort_order": sort_order, "service": _service_label(ctx)}
    elif report_type == "worst_5min":
        rows = await compute_worst_5min(
            agency_id, ctx, conn, ch=ch, limit=n_fetch, late_tolerance_sec=late_tolerance_sec
        )
        rows = listed(rows)
        intent = {"query_type": "worst_5min", "service": _service_label(ctx)}
    elif report_type == "compare_ranking":
        compared = await compute_compare_ranking(agency_id, ctx, conn, limit=None, ch=ch)
        rows_total = listed_total(compared)
        rows = listed(compared)
        intent = {"query_type": "compare_ranking"}
    elif report_type == "dow_weekend":
        rows = listed(await compute_dow_ranking(agency_id, ctx, conn, dow_group="weekend", limit=n_fetch, ch=ch))
        intent = {"query_type": "dow_ranking", "dow_group": "weekend"}
    elif report_type == "dow_weekday":
        rows = listed(await compute_dow_ranking(agency_id, ctx, conn, dow_group="weekday", limit=n_fetch, ch=ch))
        intent = {"query_type": "dow_ranking", "dow_group": "weekday"}
    elif report_type == "trend":
        # Daily series + hour-of-day heatmap for the granular Trend tab.
        series = await compute_trend_series(agency_id, ctx, conn, ch=ch)
        hourly = await compute_hourly_heatmap(agency_id, ctx, conn, ch=ch)
        dow_band = hourly_cells_to_dow_band(hourly, locale=locale)
        days = series["days"]
        if format == "csv":
            return _csv_response(report_type, days, ctx, definition)
        # Schedule-revision boundary dates — dates within this
        # range where the static feed version running that day changed —
        # so the Trend chart can mark a timetable revision instead of
        # letting a metric shift there be misread as a service-quality
        # change. Empty (not missing) when this agency has no
        # agg_schedule_revision_daily coverage at all (its ingest strategy
        # never joins static data, or its RT rows carry no static_version_id
        # yet).
        # Skipped entirely for the CSV export above, which has no chart to
        # annotate.
        revision_boundaries = await get_schedule_revision_boundaries(conn, agency_id, ctx.from_date, ctx.to_date)
        text = format_trend_text(days, ctx.from_date, ctx.to_date, locale=locale)
        return ReportResponse(
            report_type=report_type,
            rendered_at=datetime.now(timezone.utc),
            text=text,
            rows=[{"days": days, "hourly": hourly, "dow_band": dow_band, "revision_boundaries": revision_boundaries}],
            ctx=_report_ctx(ctx),
            definition=definition,
            scope_applied=report_scope_applied(report_type),
        )
    elif report_type == "dwell_run":
        payload = await compute_dwell_run_decomposition(agency_id, ctx, conn)
        # format_dwell_run_text itself is the single source of truth for the
        # available/time_band_supported "not a real zero" messages -- reusing
        # it here (rather than re-deriving the same two strings a second
        # time) is what guarantees the CSV export can never drift from the
        # JSON/text response's own wording for these two states.
        text = format_dwell_run_text(payload, locale=locale)
        if format == "csv":
            if not payload["available"] or not payload.get("time_band_supported", True):
                return _csv_response(report_type, [], ctx, definition, unavailable_message=text)
            return _csv_response(report_type, payload["routes"], ctx, definition)
        return ReportResponse(
            report_type=report_type,
            rendered_at=datetime.now(timezone.utc),
            text=text,
            rows=[payload],
            ctx=_report_ctx(ctx),
            definition=definition,
            scope_applied=report_scope_applied(report_type),
        )
    elif report_type == "council_summary":
        agency_row = await conn.fetchrow("SELECT agency_name FROM agencies WHERE agency_id = $1", agency_id)
        agency_name = agency_row["agency_name"] if agency_row else str(agency_id)
        payload = await compute_council_summary(
            agency_id,
            ctx,
            conn,
            ch,
            early_tolerance_sec=early_tolerance_sec,
            late_tolerance_sec=late_tolerance_sec,
        )
        row = (
            payload["on_time_pct"],
            payload["avg_delay_min"],
            payload["samples"],
            payload["planned_trips"],
            payload["executed_trips"],
            payload["service_delivered_pct"],
        )
        if format == "csv":
            # The CSV preamble stays Japanese-only (see _csv_response's own
            # docstring), matching format_definition_csv_line's existing
            # convention -- unlike the JSON `text` body below, which honors
            # the request's locale.
            footnotes = format_council_summary_footnotes(definition, payload, locale="ja")
            return _csv_response(report_type, [row], ctx, definition, extra_footnotes=footnotes)
        text = format_council_summary_text(payload, definition, agency_name, ctx.from_date, ctx.to_date, locale=locale)
        return ReportResponse(
            report_type=report_type,
            rendered_at=datetime.now(timezone.utc),
            text=text,
            rows=[row],
            ctx=_report_ctx(ctx),
            definition=definition,
            scope_applied=report_scope_applied(report_type),
            delivered_scope_applied=_COUNCIL_DELIVERED_SCOPE,
        )
    elif report_type == "delay_certificate":
        threshold = DEFAULT_DELAY_CERTIFICATE_THRESHOLD_SEC if threshold_sec is None else threshold_sec
        # The CSV is Japanese-only, its rows' day markers and its preamble alike,
        # matching council_summary's CSV branch and format_definition_csv_line.
        rows, rows_total = await compute_delay_certificate(
            agency_id, ctx, conn, ch, threshold_sec=threshold, limit=n, locale="ja" if format == "csv" else locale
        )
        if format == "csv":
            footnotes = format_delay_certificate_footnotes(threshold, len(rows), rows_total, locale="ja")
            return _csv_response(report_type, rows, ctx, definition, extra_footnotes=footnotes)
        observed: bool | None = True
        if rows_total == 0:
            # Only tells an empty list's two causes apart, so a failed probe
            # leaves that unknown rather than failing a complete response.
            try:
                observed = await delay_certificate_observed(agency_id, ctx, ch)
            except Exception:
                _log.warning("delay_certificate: observed probe failed for agency %s", agency_id, exc_info=True)
                observed = None
        text = format_delay_certificate_text(rows, threshold, rows_total, locale=locale, observed=observed)
        return ReportResponse(
            report_type=report_type,
            rendered_at=datetime.now(timezone.utc),
            text=text,
            rows=rows,
            ctx=_report_ctx(ctx),
            definition=definition,
            scope_applied=report_scope_applied(report_type),
            rows_total=rows_total,
            observed=observed,
        )
    else:
        raise HTTPException(status_code=500, detail="unreachable")

    if format == "csv":
        return _csv_response(report_type, rows, ctx, definition)

    text = format_result(intent["query_type"], rows, intent, locale=locale)
    return ReportResponse(
        report_type=report_type,
        rendered_at=datetime.now(timezone.utc),
        text=text,
        rows=rows,
        ctx=_report_ctx(ctx),
        definition=definition,
        scope_applied=report_scope_applied(report_type),
        rows_total=rows_total,
        reliable_min_samples=RANKING_MIN_SAMPLES if report_type in _FLOORED_TYPES else None,
        retired_rows=left_out_rows.get(RETIRED),
        zero_delay_rows=left_out_rows.get(ZERO_DELAY),
        sort=table_sort if report_type == "route_table" else None,
        order=table_order if report_type == "route_table" else None,
    )
