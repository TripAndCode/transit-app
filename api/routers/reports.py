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
from pipeline.query.formatter import format_result, format_trend_text
from pipeline.reports import (
    compute_compare_ranking,
    compute_dow_ranking,
    compute_hourly_heatmap,
    compute_on_time,
    compute_ranking,
    compute_trend_series,
    compute_worst_5min,
)
from pipeline.reports.forecast import (
    hourly_cells_to_dow_band,
    summarize_agency_overview,
    summarize_expected_delay_heatmap,
)

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
        "SELECT dow, hour, "
        "SUM(avg_min * samples) / NULLIF(SUM(samples), 0) AS avg_min, "
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
        "SELECT d.date, d.route_code, "
        "  SUM(d.avg_delay_sec * d.samples) / NULLIF(SUM(d.samples), 0) / 60.0 AS avg_min "
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
        "SELECT dow, hour, "
        "SUM(avg_min * samples) / NULLIF(SUM(samples), 0) AS avg_min, "
        "SUM(samples)::int AS samples "
        "FROM agg_route_hour_dow "
        "WHERE agency_id = $1 AND avg_min IS NOT NULL AND samples > 0 "
        "GROUP BY dow, hour",
        agency_id,
    )
    route_rows = await conn.fetch(
        "WITH ra AS ("
        "  SELECT route_code, "
        "    SUM(avg_min * samples) / NULLIF(SUM(samples), 0) AS avg_min, "
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


# Column headers used when emitting CSV. Japanese labels for operator-facing
# downloads. Must match the row tuple shape produced by each compute_*.
_REPORT_CSV_COLUMNS: dict[str, list[str]] = {
    "ranking": ["系統コード", "種別", "平均遅延(分)", "中央値(分)", "p90(分)", "観測数"],
    "ranking_best": ["系統コード", "種別", "平均遅延(分)", "中央値(分)", "p90(分)", "観測数"],
    "on_time": ["系統コード", "種別", "定時率(%)", "平均遅延(分)", "観測数"],
    "worst_5min": ["系統コード", "種別", "5分超回数", "平均遅延(分)", "観測数"],
    "compare_ranking": ["系統コード", "平日(分)", "土日祝(分)", "差(絶対値)", "差(符号付き)"],
    "dow_weekend": ["系統コード", "種別", "曜日区分", "平均遅延(分)", "観測数"],
    "dow_weekday": ["系統コード", "種別", "曜日区分", "平均遅延(分)", "観測数"],
    "trend": ["日付", "平均遅延(分)", "観測数", "悪化系統トップ3"],
}


def _csv_response(report_type: str, rows: list, ctx: RangeCtx) -> StreamingResponse:
    """Stream a UTF-8 BOM CSV (BOM lets Excel auto-detect Japanese encoding)."""
    cols = _REPORT_CSV_COLUMNS.get(report_type, [])
    buf = io.StringIO()
    buf.write("﻿")  # BOM
    w = csv.writer(buf)
    w.writerow(cols)
    if report_type == "trend":
        for d in rows:
            offenders = "; ".join(o.get("route_code", "") for o in (d.get("top_offenders") or []))
            w.writerow([d.get("date"), d.get("avg_min"), d.get("samples"), offenders])
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
    agency_id: int = Depends(get_agency),
    conn=Depends(get_conn),
    ch=Depends(get_ch),
    ctx: RangeCtx = Depends(get_range_ctx),
    locale: str = Depends(get_locale),
):
    """Compute the named report live and render it."""
    if report_type not in _REPORT_TYPES:
        raise HTTPException(status_code=404, detail=f"Unknown report type '{report_type}'")

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
        rows = await compute_on_time(agency_id, ctx, conn, ch=ch, limit=n)
        intent = {"query_type": "on_time", "limit": n}
    elif report_type == "worst_5min":
        rows = await compute_worst_5min(agency_id, ctx, conn, ch=ch, limit=n)
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
            return _csv_response(report_type, days, ctx)
        text = format_trend_text(days, ctx.from_date, ctx.to_date, locale=locale)
        return ReportResponse(
            report_type=report_type,
            rendered_at=datetime.now(timezone.utc),
            text=text,
            rows=[{"days": days, "hourly": hourly, "dow_band": dow_band}],
            ctx=_ctx_payload(ctx),
        )
    else:
        raise HTTPException(status_code=500, detail="unreachable")

    if format == "csv":
        return _csv_response(report_type, rows, ctx)

    text = format_result(intent["query_type"], rows, intent, locale=locale)
    return ReportResponse(
        report_type=report_type,
        rendered_at=datetime.now(timezone.utc),
        text=text,
        rows=rows,
        ctx=_ctx_payload(ctx),
    )
