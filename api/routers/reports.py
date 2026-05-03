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

from api.deps import get_agency, get_conn
from api.middleware.ratelimit import FREE_LIMIT, PRO_LIMIT, limiter
from api.range import RangeCtx, get_range_ctx
from pipeline.query.formatter import format_result
from pipeline.reports import (
    compute_compare_ranking,
    compute_dow_ranking,
    compute_on_time,
    compute_ranking,
    compute_trend_series,
    compute_worst_5min,
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
    report_type: str
    rendered_at: datetime
    text: str
    rows: list
    ctx: ReportCtx


def _ctx_payload(ctx: RangeCtx) -> ReportCtx:
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


# Column headers used when emitting CSV. Must match the row tuple shape
# produced by each compute_* function.
_REPORT_CSV_COLUMNS: dict[str, list[str]] = {
    "ranking": ["route_code", "service_type", "avg_min", "p50_min", "p90_min", "samples"],
    "ranking_best": ["route_code", "service_type", "avg_min", "p50_min", "p90_min", "samples"],
    "on_time": ["route_code", "service_type", "on_time_pct", "avg_min", "samples"],
    "worst_5min": ["route_code", "service_type", "late5_count", "avg_min", "samples"],
    "compare_ranking": ["route_code", "heijitsu_min", "kyujitsu_min", "abs_delta", "signed_delta"],
    "dow_weekend": ["route_code", "service_type", "dow", "avg_min", "samples"],
    "dow_weekday": ["route_code", "service_type", "dow", "avg_min", "samples"],
    "trend": ["date", "avg_min", "samples", "top_offender_routes"],
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
    format: str | None = Query(default=None, regex="^(json|csv)$"),
    agency_id: int = Depends(get_agency),
    conn=Depends(get_conn),
    ctx: RangeCtx = Depends(get_range_ctx),
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
        # Daily series for the chart-driven Trend tab.
        series = await compute_trend_series(agency_id, ctx, conn)
        days = series["days"]
        if format == "csv":
            return _csv_response(report_type, days, ctx)
        if days:
            avg = sum(d["avg_min"] or 0 for d in days) / len(days)
            text = f"【日次トレンド({ctx.from_date} 〜 {ctx.to_date})】\n平均: {avg:.2f}分 / 観測日数: {len(days)}日"
        else:
            text = "選択した期間にデータがありません。"
        return ReportResponse(
            report_type=report_type,
            rendered_at=datetime.now(timezone.utc),
            text=text,
            rows=days,
            ctx=_ctx_payload(ctx),
        )
    else:
        raise HTTPException(status_code=500, detail="unreachable")

    if format == "csv":
        return _csv_response(report_type, rows, ctx)

    text = format_result(intent["query_type"], rows, intent)
    return ReportResponse(
        report_type=report_type,
        rendered_at=datetime.now(timezone.utc),
        text=text,
        rows=rows,
        ctx=_ctx_payload(ctx),
    )
