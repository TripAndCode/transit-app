"""Reports endpoints (v2): live queries scoped to the user's RangeCtx.

Each report is computed on demand from ``updates`` so the global time-range
/ DOW / time-band filter changes the numbers. ``rendered_at`` reflects the
moment the request was served. The ``snapshots`` table from v1 is gone.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

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
    from_date: str
    to_date: str
    dow: str
    time_band: str


class ReportResponse(BaseModel):
    report_type: str
    rendered_at: datetime
    text: str
    rows: list
    ctx: ReportCtx


def _ctx_payload(ctx: RangeCtx) -> ReportCtx:
    return ReportCtx(
        from_date=ctx.from_date.isoformat(),
        to_date=ctx.to_date.isoformat(),
        dow=ctx.dow,
        time_band=ctx.time_band,
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


@router.get("/reports/{report_type}", response_model=ReportResponse)
@limiter.limit(f"{FREE_LIMIT};{PRO_LIMIT}")
async def get_report(
    request: Request,
    report_type: str,
    limit: int | None = Query(default=None, ge=1),
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
        # Daily series for the chart-driven Trend tab. The legacy "trend" text
        # formatter (route-by-route 14d vs prev 14d comparison) lives behind
        # /api/{id}/trend's executor path; this surface returns the chart
        # series as `rows` and a brief Japanese summary as `text`.
        series = await compute_trend_series(agency_id, ctx, conn)
        days = series["days"]
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

    text = format_result(intent["query_type"], rows, intent)
    return ReportResponse(
        report_type=report_type,
        rendered_at=datetime.now(timezone.utc),
        text=text,
        rows=rows,
        ctx=_ctx_payload(ctx),
    )


@router.get("/trend")
@limiter.limit(f"{FREE_LIMIT};{PRO_LIMIT}")
async def trend(
    request: Request,
    agency_id: int = Depends(get_agency),
    conn=Depends(get_conn),
    ctx: RangeCtx = Depends(get_range_ctx),
):
    """Daily delay series for the chart, scoped to ctx.

    Returns ``{ days: [{ date, avg_min, samples, top_offenders[] }], ctx }``.
    """
    series = await compute_trend_series(agency_id, ctx, conn)
    return {"days": series["days"], "ctx": _ctx_payload(ctx).model_dump()}
