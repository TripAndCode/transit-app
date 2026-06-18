"""Reports endpoints (v2): live queries scoped to the user's RangeCtx.

Each report is computed on demand from ``updates`` so the global time-range
/ DOW / time-band filter changes the numbers. ``rendered_at`` reflects the
moment the request was served. The ``snapshots`` table from v1 is gone.
"""

import csv
import io
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from api.deps import get_agency, get_conn, get_locale
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
    summarize_expected_delay,
    summarize_expected_delay_profile,
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


class ForecastResponse(BaseModel):
    """Payload for ``GET /forecast`` — the typical (expected) delay for a slot."""

    route: str
    service_type: str
    hour: int
    expected_avg_min: float | None
    samples: int
    low_confidence: bool
    disclaimer: str


class ForecastProfileHour(BaseModel):
    """One hour (0–23) of the expected-delay profile."""

    hour: int
    expected_avg_min: float | None
    samples: int
    low_confidence: bool


class ForecastProfileResponse(BaseModel):
    """Payload for ``GET /forecast/profile`` — expected delay across all 24 hours."""

    route: str
    service_type: str
    hours: list[ForecastProfileHour]
    disclaimer: str


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


@router.get("/forecast", response_model=ForecastResponse)
@limiter.limit(f"{FREE_LIMIT};{PRO_LIMIT}")
async def forecast(
    request: Request,
    route: str = Query(..., min_length=1),
    service_type: str = Query(..., min_length=1),
    hour: int = Query(..., ge=0, le=23),
    agency_id: int = Depends(get_agency),
    conn=Depends(get_conn),
    locale: str = Depends(get_locale),
):
    """Typical ("expected") delay for a route at a service type + hour.

    Reads the precomputed ``agg_route_hour`` baseline — a seasonal-naive lookup,
    NOT a prediction. The response always carries a plain-language disclaimer.
    """
    rows = await conn.fetch(
        "SELECT avg_min, samples FROM agg_route_hour "
        "WHERE agency_id = $1 AND route_code = $2 AND service_type = $3 "
        "AND EXTRACT(HOUR FROM scheduled_time)::int = $4",
        agency_id,
        route,
        service_type,
        hour,
    )
    return summarize_expected_delay(rows, route, service_type, hour, locale)


@router.get("/forecast/profile", response_model=ForecastProfileResponse)
@limiter.limit(f"{FREE_LIMIT};{PRO_LIMIT}")
async def forecast_profile(
    request: Request,
    route: str = Query(..., min_length=1),
    service_type: str = Query(..., min_length=1),
    agency_id: int = Depends(get_agency),
    conn=Depends(get_conn),
    locale: str = Depends(get_locale),
):
    """Expected delay by hour (0–23) for a route at a service type.

    Pools the precomputed ``agg_route_hour`` baseline to the hour (the
    sample-weighted mean equals the exact pooled mean). Seasonal-naive, NOT a
    prediction — the response always carries a plain-language disclaimer.
    """
    rows = await conn.fetch(
        "SELECT EXTRACT(HOUR FROM scheduled_time)::int AS hour, "
        "SUM(avg_min * samples) / NULLIF(SUM(samples), 0) AS avg_min, "
        "SUM(samples)::int AS samples "
        "FROM agg_route_hour "
        "WHERE agency_id = $1 AND route_code = $2 AND service_type = $3 "
        "AND avg_min IS NOT NULL AND samples > 0 "
        "GROUP BY 1 ORDER BY 1",
        agency_id,
        route,
        service_type,
    )
    return summarize_expected_delay_profile(rows, route, service_type, locale)


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
        rows = await compute_ranking(agency_id, ctx, conn, sort_order="desc", limit=n)
        intent = {"query_type": "ranking", "limit": n}
    elif report_type == "ranking_best":
        rows = await compute_ranking(agency_id, ctx, conn, sort_order="asc", limit=n)
        intent = {"query_type": "ranking", "limit": n, "sort_order": "asc"}
    elif report_type == "on_time":
        rows = await compute_on_time(agency_id, ctx, conn, limit=n)
        intent = {"query_type": "on_time", "limit": n}
    elif report_type == "worst_5min":
        rows = await compute_worst_5min(agency_id, ctx, conn, limit=n)
        intent = {"query_type": "worst_5min", "limit": n}
    elif report_type == "compare_ranking":
        rows = await compute_compare_ranking(agency_id, ctx, conn, limit=n)
        intent = {"query_type": "compare_ranking", "limit": n}
    elif report_type == "dow_weekend":
        rows = await compute_dow_ranking(agency_id, ctx, conn, dow_group="weekend", limit=n)
        intent = {"query_type": "dow_ranking", "dow_group": "weekend", "limit": n}
    elif report_type == "dow_weekday":
        rows = await compute_dow_ranking(agency_id, ctx, conn, dow_group="weekday", limit=n)
        intent = {"query_type": "dow_ranking", "dow_group": "weekday", "limit": n}
    elif report_type == "trend":
        # Daily series + hour-of-day heatmap for the granular Trend tab.
        series = await compute_trend_series(agency_id, ctx, conn)
        hourly = await compute_hourly_heatmap(agency_id, ctx, conn)
        days = series["days"]
        if format == "csv":
            return _csv_response(report_type, days, ctx)
        text = format_trend_text(days, ctx.from_date, ctx.to_date, locale=locale)
        return ReportResponse(
            report_type=report_type,
            rendered_at=datetime.now(timezone.utc),
            text=text,
            rows=[{"days": days, "hourly": hourly}],
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
