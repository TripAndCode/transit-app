"""Reports endpoints (v2): live queries scoped to the user's RangeCtx.

Each report is computed on demand from ``updates`` so the global time-range
/ DOW / time-band filter changes the numbers. ``rendered_at`` reflects the
moment the request was served. The ``snapshots`` table from v1 is gone.
"""

import csv
import io
from datetime import datetime, timezone

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from api.deps import get_agency, get_ch, get_conn, get_locale
from api.middleware.ratelimit import FREE_LIMIT, PRO_LIMIT, limiter
from api.range import RangeCtx, get_range_ctx
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
    ON_TIME_PRESETS,
    DefinitionMeta,
    compute_compare_ranking,
    compute_council_summary,
    compute_delay_certificate,
    compute_dow_ranking,
    compute_dwell_run_decomposition,
    compute_hourly_heatmap,
    compute_on_time,
    compute_ranking,
    compute_trend_series,
    compute_worst_5min,
    format_definition_csv_line,
    resolve_definition_meta,
)
from pipeline.reports.forecast import (
    hourly_cells_to_dow_band,
    summarize_agency_overview,
    summarize_expected_delay_heatmap,
)
from pipeline.reports.schedule_revision import get_schedule_revision_boundaries
from pipeline.reports.suggest import compute_suggestion
from pipeline.stats import annotate_on_time_pct_confidence

router = APIRouter(prefix="/api/{agency_id}", tags=["reports"])

# Static metadata for the listing endpoint. Ordered for sidebar display.
_REPORT_TYPES = (
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


class ReportMeta(BaseModel):
    """Listing entry returned by ``GET /reports``."""

    report_type: str
    rendered_at: datetime


class ReportCtx(BaseModel):
    """Echoed back to clients with the frontend's preferred ``from``/``to`` keys."""

    from_: str = Field(serialization_alias="from")
    to: str
    dow: str
    time_band: str
    service: str = "all"
    routes: list[str] = []


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


def _ctx_payload(ctx: RangeCtx) -> ReportCtx:
    """Project the internal ``RangeCtx`` into the client-facing ``ReportCtx``."""
    return ReportCtx(
        from_=ctx.from_date.isoformat(),
        to=ctx.to_date.isoformat(),
        dow=ctx.dow,
        time_band=ctx.time_band,
        service=ctx.service,
        routes=list(ctx.routes),
    )


@router.get("/reports", response_model=list[ReportMeta])
@limiter.limit(f"{FREE_LIMIT};{PRO_LIMIT}")
async def list_reports(
    request: Request,
    agency_id: int = Depends(get_agency),
    conn=Depends(get_conn),
):
    """Static list of report types. ``rendered_at`` is request time."""
    del conn  # unused; keep for parity with get_report
    now = datetime.now(timezone.utc)
    return [{"report_type": rt, "rendered_at": now} for rt in _REPORT_TYPES]


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


@router.get("/reports/suggest", response_model=SuggestionResponse | None)
@limiter.limit(f"{FREE_LIMIT};{PRO_LIMIT}")
async def get_suggestion(
    request: Request,
    agency_id: int = Depends(get_agency),
    conn=Depends(get_conn),
    ch=Depends(get_ch),
    locale: str = Depends(get_locale),
    exclude: list[str] = Query(default=[]),
):
    """One rule-based 'go look at this' suggestion for the Analysis tab's
    Insight Panel. ``exclude`` entries are ``"report_type:route_code"``
    pairs the frontend has already shown this session (sessionStorage-backed,
    stateless here). Returns ``null`` when every rule's candidates are
    excluded or the agency has no data at all -- the frontend renders its
    own calm 'no signal' copy for that case, not this endpoint.
    """
    exclude_set: frozenset[tuple[str, str]] = frozenset(
        (report_type, route_code) for item in exclude if ":" in item for report_type, route_code in [item.split(":", 1)]
    )
    result = await compute_suggestion(agency_id, conn, ch, exclude=exclude_set, locale=locale)
    return result


class ForecastHeatmapCell(BaseModel):
    """One day-of-week × hour cell of the forecast heatmap."""

    dow: int
    hour: int
    expected_avg_min: float | None
    samples: int
    low_confidence: bool


class ForecastHeatmapResponse(BaseModel):
    """Payload for ``GET /forecast/heatmap`` — the full 7×24 day×hour grid."""

    route: str
    cells: list[ForecastHeatmapCell]
    disclaimer: str


@router.get("/forecast/heatmap", response_model=ForecastHeatmapResponse)
@limiter.limit(f"{FREE_LIMIT};{PRO_LIMIT}")
async def forecast_heatmap(
    request: Request,
    route: str = Query(..., min_length=1),
    agency_id: int = Depends(get_agency),
    conn=Depends(get_conn),
    locale: str = Depends(get_locale),
):
    """Expected delay by day-of-week (ISODOW 1=Mon..7=Sun) × hour (0..23) for a
    route, pooled across service types (sample-weighted = exact pooled mean).
    Seasonal-naive baseline, NOT a prediction; carries a disclaimer.
    """
    rows = await conn.fetch(
        # sum_delay_sec is nullable (unlike samples); FILTER both sides to the
        # same row population so a pre-backfill NULL row can't inflate the
        # denominator without contributing to the numerator (see
        # pipeline/reports/rankings.py's identical rationale).
        "SELECT dow, hour, "
        "(SUM(sum_delay_sec) FILTER (WHERE sum_delay_sec IS NOT NULL)::numeric "
        "    / NULLIF(SUM(samples) FILTER (WHERE sum_delay_sec IS NOT NULL), 0) / 60.0) AS avg_min, "
        "SUM(samples)::int AS samples "
        "FROM agg_route_hour_dow "
        "WHERE agency_id = $1 AND route_code = $2 AND avg_min IS NOT NULL AND samples > 0 "
        "GROUP BY dow, hour ORDER BY dow, hour",
        agency_id,
        route,
    )
    return summarize_expected_delay_heatmap(rows, route, locale)


class ForecastOverviewGridCell(BaseModel):
    """One day-of-week × time-band cell of the agency overview grid."""

    dow: int
    band: str
    expected_avg_min: float | None
    samples: int
    low_confidence: bool


class ForecastOverviewWorst(BaseModel):
    """The single worst (highest pooled delay) window agency-wide."""

    dow: int
    band: str
    expected_avg_min: float
    samples: int


class ForecastOverviewRoute(BaseModel):
    """One route in the delay-ranked list."""

    route_code: str
    route_name: str
    expected_avg_min: float
    samples: int
    low_confidence: bool
    # Last 7 analyzed calendar days' average delay for this route, oldest
    # first (from agg_route_daily — a different, seasonally-pooled source
    # than expected_avg_min above). Empty when the route has no recent
    # agg_route_daily rows (e.g. it hasn't run in the last week).
    recent_daily: list[float] = []


class ForecastOverviewResponse(BaseModel):
    """Payload for ``GET /forecast/overview`` — agency-wide landing view."""

    grid: list[ForecastOverviewGridCell]
    worst: ForecastOverviewWorst | None
    routes: list[ForecastOverviewRoute]
    disclaimer: str


async def _fetch_recent_daily_rows(conn: asyncpg.Connection, agency_id: int) -> list[asyncpg.Record]:
    """Last 7 analyzed calendar days per route, from agg_route_daily (real
    per-date rows) — a different table than route_rows in forecast_overview
    (which pools ALL time from the seasonal agg_route_hour_dow). Powers each
    route's sparkline. NULL MAX(date) (brand-new agency, no agg rows yet)
    makes the WHERE clause's date comparisons false, so this safely returns
    zero rows rather than erroring.
    """
    return await conn.fetch(
        "WITH latest AS ("
        "  SELECT MAX(date) AS d FROM agg_route_daily WHERE agency_id = $1"
        ") "
        # sum_delay_sec is nullable (unlike samples); FILTER both sides to the
        # same row population — see forecast_heatmap's identical rationale.
        "SELECT d.date, d.route_code, "
        "  (SUM(d.sum_delay_sec) FILTER (WHERE d.sum_delay_sec IS NOT NULL)::numeric "
        "      / NULLIF(SUM(d.samples) FILTER (WHERE d.sum_delay_sec IS NOT NULL), 0) / 60.0) AS avg_min "
        "FROM agg_route_daily d, latest "
        "WHERE d.agency_id = $1 AND d.date > latest.d - 7 AND d.date <= latest.d "
        "GROUP BY d.date, d.route_code "
        "ORDER BY d.route_code, d.date",
        agency_id,
    )


@router.get("/forecast/overview", response_model=ForecastOverviewResponse)
@limiter.limit(f"{FREE_LIMIT};{PRO_LIMIT}")
async def forecast_overview(
    request: Request,
    agency_id: int = Depends(get_agency),
    conn=Depends(get_conn),
    locale: str = Depends(get_locale),
):
    """Agency-wide expected delay: a 7-day × time-band grid (pooled across all
    routes), the worst window, and a delay-ranked route list. Seasonal-naive
    baseline, NOT a prediction; carries a disclaimer. Re-pools agg_route_hour_dow
    (no dedicated aggregate — the table is small enough to pool on read).
    """
    grid_rows = await conn.fetch(
        # sum_delay_sec is nullable (unlike samples); FILTER both sides to the
        # same row population — see forecast_heatmap's identical rationale.
        "SELECT dow, hour, "
        "(SUM(sum_delay_sec) FILTER (WHERE sum_delay_sec IS NOT NULL)::numeric "
        "    / NULLIF(SUM(samples) FILTER (WHERE sum_delay_sec IS NOT NULL), 0) / 60.0) AS avg_min, "
        "SUM(samples)::int AS samples "
        "FROM agg_route_hour_dow "
        "WHERE agency_id = $1 AND avg_min IS NOT NULL AND samples > 0 "
        "GROUP BY dow, hour",
        agency_id,
    )
    route_rows = await conn.fetch(
        "WITH ra AS ("
        "  SELECT route_code, "
        "    (SUM(sum_delay_sec) FILTER (WHERE sum_delay_sec IS NOT NULL)::numeric "
        "        / NULLIF(SUM(samples) FILTER (WHERE sum_delay_sec IS NOT NULL), 0) / 60.0) AS avg_min, "
        "    SUM(samples)::int AS samples "
        "  FROM agg_route_hour_dow "
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
    # degrades to no sparklines instead of 500ing the whole response.
    try:
        recent_daily_rows = await _fetch_recent_daily_rows(conn, agency_id)
    except Exception:
        recent_daily_rows = []
    return summarize_agency_overview(grid_rows, route_rows, recent_daily_rows, locale)


# Marker for a row's trailing `low_confidence` flag in the "on_time" CSV
# export, mirroring the caveat mark frontend/src/components/ReportTable.tsx's
# fmtConfidence and frontend/src/tabs/ask/RichResult.tsx render for the same
# signal (a short mark when true, blank when false) rather than the raw
# Python bool's str() form.
_LOW_CONFIDENCE_CSV_MARK = "幅あり"

# Column headers used when emitting CSV. Japanese labels for operator-facing
# downloads. Must match the row tuple shape produced by each compute_*.
_REPORT_CSV_COLUMNS: dict[str, list[str]] = {
    "ranking": ["系統コード", "種別", "平均遅延(分)", "中央値(分)", "p90(分)", "観測数"],
    "ranking_best": ["系統コード", "種別", "平均遅延(分)", "中央値(分)", "p90(分)", "観測数"],
    "on_time": ["系統コード", "種別", "定時率(%)", "平均遅延(分)", "観測数", "確信度低"],
    "worst_5min": ["系統コード", "種別", "5分超回数", "平均遅延(分)", "観測数"],
    "compare_ranking": ["系統コード", "平日(分)", "土日祝(分)", "差(絶対値)", "差(符号付き)"],
    "dow_weekend": ["系統コード", "種別", "曜日区分", "平均遅延(分)", "観測数"],
    "dow_weekday": ["系統コード", "種別", "曜日区分", "平均遅延(分)", "観測数"],
    "trend": ["日付", "平均遅延(分)", "7日移動平均(分)", "観測数", "悪化系統トップ3"],
    "dwell_run": ["系統コード", "種別", "滞留観測数", "滞留平均(秒)", "走行観測数", "走行平均(秒)"],
    "council_summary": ["定時率(%)", "平均遅延(分)", "観測数", "計画本数", "運行本数", "運行実績率(%)"],
    "delay_certificate": ["事業者名", "系統コード", "種別", "日付", "定刻", "実績時刻", "遅延(秒)"],
}


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
    elif report_type == "on_time":
        for r in rows:
            *lead, low_confidence = r
            w.writerow([*lead, _LOW_CONFIDENCE_CSV_MARK if low_confidence else ""])
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
    fname = f"{report_type}_{ctx.from_date}_{ctx.to_date}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.get("/reports/{report_type}", response_model=ReportResponse)
@limiter.limit(f"{FREE_LIMIT};{PRO_LIMIT}")
async def get_report(
    request: Request,
    report_type: str,
    limit: int | None = Query(default=None, ge=1),
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
    threshold_sec: int | None = Query(
        default=None,
        ge=0,
        description="delay_certificate only: a departure's dep_delay must STRICTLY EXCEED this "
        "many seconds to be included. Defaults to "
        "pipeline.reports.council.DEFAULT_DELAY_CERTIFICATE_THRESHOLD_SEC.",
    ),
    agency_id: int = Depends(get_agency),
    conn=Depends(get_conn),
    ch=Depends(get_ch),
    ctx: RangeCtx = Depends(get_range_ctx),
    locale: str = Depends(get_locale),
):
    """Compute the named report live and render it."""
    if report_type not in _REPORT_TYPES:
        raise HTTPException(status_code=404, detail=f"Unknown report type '{report_type}'")

    if preset is not None:
        if early_tolerance_sec is not None or late_tolerance_sec is not None:
            raise HTTPException(
                status_code=400, detail="preset cannot be combined with early_tolerance_sec/late_tolerance_sec"
            )
        if preset not in ON_TIME_PRESETS:
            raise HTTPException(status_code=400, detail=f"Unknown preset '{preset}'")
        early_tolerance_sec, late_tolerance_sec = ON_TIME_PRESETS[preset]
    if early_tolerance_sec is not None and report_type not in ("on_time", "council_summary"):
        raise HTTPException(
            status_code=400, detail="early_tolerance_sec only applies to the on_time/council_summary reports"
        )
    if late_tolerance_sec is not None and report_type not in ("on_time", "worst_5min", "council_summary"):
        raise HTTPException(
            status_code=400, detail="late_tolerance_sec only applies to the on_time/worst_5min/council_summary reports"
        )
    if threshold_sec is not None and report_type != "delay_certificate":
        raise HTTPException(status_code=400, detail="threshold_sec only applies to the delay_certificate report")

    # Resolved from the same (now-validated) params compute_on_time/
    # compute_worst_5min themselves consume below, so this can never show a
    # tolerance different from the one the rows were actually computed with.
    definition = resolve_definition_meta(report_type, early_tolerance_sec, late_tolerance_sec)

    n = limit or 100
    intent: dict = {}
    rows: list

    if report_type == "ranking":
        rows = await compute_ranking(agency_id, ctx, conn, ch=ch, sort_order="desc", limit=n)
        intent = {"query_type": "ranking", "limit": n}
    elif report_type == "ranking_best":
        rows = await compute_ranking(agency_id, ctx, conn, ch=ch, sort_order="asc", limit=n)
        intent = {"query_type": "ranking", "limit": n, "sort_order": "asc"}
    elif report_type == "on_time":
        rows = await compute_on_time(
            agency_id,
            ctx,
            conn,
            ch=ch,
            limit=n,
            early_tolerance_sec=early_tolerance_sec,
            late_tolerance_sec=late_tolerance_sec,
        )
        # Appends a `low_confidence` bool (95% Wilson interval too wide to
        # trust the percentage) as a display-layer annotation — doesn't
        # change compute_on_time's own 5-tuple contract, so pooling callers
        # (pipeline.reports.suggest) are unaffected. See pipeline/stats.py.
        rows = annotate_on_time_pct_confidence(rows)
        intent = {"query_type": "on_time", "limit": n}
    elif report_type == "worst_5min":
        rows = await compute_worst_5min(agency_id, ctx, conn, ch=ch, limit=n, late_tolerance_sec=late_tolerance_sec)
        intent = {"query_type": "worst_5min", "limit": n}
    elif report_type == "compare_ranking":
        rows = await compute_compare_ranking(agency_id, ctx, conn, limit=n, ch=ch)
        intent = {"query_type": "compare_ranking", "limit": n}
    elif report_type == "dow_weekend":
        rows = await compute_dow_ranking(agency_id, ctx, conn, dow_group="weekend", limit=n, ch=ch)
        intent = {"query_type": "dow_ranking", "dow_group": "weekend", "limit": n}
    elif report_type == "dow_weekday":
        rows = await compute_dow_ranking(agency_id, ctx, conn, dow_group="weekday", limit=n, ch=ch)
        intent = {"query_type": "dow_ranking", "dow_group": "weekday", "limit": n}
    elif report_type == "trend":
        # Daily series + hour-of-day heatmap for the granular Trend tab.
        series = await compute_trend_series(agency_id, ctx, conn, ch=ch)
        hourly = await compute_hourly_heatmap(agency_id, ctx, conn, ch=ch)
        dow_band = hourly_cells_to_dow_band(hourly, locale=locale)
        days = series["days"]
        if format == "csv":
            return _csv_response(report_type, days, ctx, definition)
        # Schedule-revision boundary dates (item 98) — dates within this
        # range where the static feed version running that day changed —
        # so the Trend chart can mark a timetable revision instead of
        # letting a metric shift there be misread as a service-quality
        # change. Empty (not missing) when this agency has no
        # agg_schedule_revision_daily coverage at all (its ingest strategy
        # never joins static data, or no reload has happened since item 88).
        # Skipped entirely for the CSV export above, which has no chart to
        # annotate.
        revision_boundaries = await get_schedule_revision_boundaries(conn, agency_id, ctx.from_date, ctx.to_date)
        text = format_trend_text(days, ctx.from_date, ctx.to_date, locale=locale)
        return ReportResponse(
            report_type=report_type,
            rendered_at=datetime.now(timezone.utc),
            text=text,
            rows=[{"days": days, "hourly": hourly, "dow_band": dow_band, "revision_boundaries": revision_boundaries}],
            ctx=_ctx_payload(ctx),
            definition=definition,
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
            ctx=_ctx_payload(ctx),
            definition=definition,
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
            ctx=_ctx_payload(ctx),
            definition=definition,
        )
    elif report_type == "delay_certificate":
        threshold = DEFAULT_DELAY_CERTIFICATE_THRESHOLD_SEC if threshold_sec is None else threshold_sec
        rows = await compute_delay_certificate(agency_id, ctx, conn, ch, threshold_sec=threshold, limit=n)
        text = format_delay_certificate_text(rows, threshold, locale=locale)
        if format == "csv":
            # Japanese-only preamble, matching council_summary's CSV branch
            # and format_definition_csv_line's existing convention.
            footnotes = format_delay_certificate_footnotes(threshold, locale="ja")
            return _csv_response(report_type, rows, ctx, definition, extra_footnotes=footnotes)
        return ReportResponse(
            report_type=report_type,
            rendered_at=datetime.now(timezone.utc),
            text=text,
            rows=rows,
            ctx=_ctx_payload(ctx),
            definition=definition,
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
        ctx=_ctx_payload(ctx),
        definition=definition,
    )
