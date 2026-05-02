from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from api.deps import get_agency, get_conn
from api.middleware.ratelimit import FREE_LIMIT, PRO_LIMIT, limiter
from pipeline.query.executor import execute

router = APIRouter(prefix="/api/{agency_id}", tags=["reports"])

_REPORT_INTENTS: dict[str, dict] = {
    "ranking":         {"query_type": "ranking",         "limit": 100},
    "ranking_best":    {"query_type": "ranking",         "limit": 100, "sort_order": "asc"},
    "on_time":         {"query_type": "on_time",         "limit": 100},
    "worst_5min":      {"query_type": "worst_5min",      "limit": 100},
    "trend":           {"query_type": "trend"},
    "compare_ranking": {"query_type": "compare_ranking", "limit": 100},
    "dow_weekend":     {"query_type": "dow_ranking",     "dow_group": "weekend", "limit": 100},
    "dow_weekday":     {"query_type": "dow_ranking",     "dow_group": "weekday", "limit": 100},
}


class ReportMeta(BaseModel):
    report_type: str
    rendered_at: datetime


class ReportResponse(BaseModel):
    report_type: str
    rendered_at: datetime
    text: str
    rows: list


@router.get("/reports", response_model=list[ReportMeta])
@limiter.limit(f"{FREE_LIMIT};{PRO_LIMIT}")
async def list_reports(
    request: Request,
    agency_id: int = Depends(get_agency),
    conn=Depends(get_conn),
):
    rows = await conn.fetch(
        "SELECT report_type, rendered_at FROM snapshots "
        "WHERE agency_id=$1 ORDER BY report_type",
        agency_id,
    )
    return [{"report_type": r["report_type"], "rendered_at": r["rendered_at"]} for r in rows]


@router.get("/reports/{report_type}", response_model=ReportResponse)
@limiter.limit(f"{FREE_LIMIT};{PRO_LIMIT}")
async def get_report(
    request: Request,
    report_type: str,
    limit: int | None = Query(default=None, ge=1),
    agency_id: int = Depends(get_agency),
    conn=Depends(get_conn),
):
    snap = await conn.fetchrow(
        "SELECT report_type, rendered_at, text FROM snapshots "
        "WHERE agency_id=$1 AND report_type=$2",
        agency_id, report_type,
    )
    if not snap:
        raise HTTPException(
            status_code=404,
            detail=f"Report '{report_type}' not found for agency {agency_id}",
        )

    text = snap["text"]
    if limit is not None:
        lines = text.split("\n")
        text = "\n".join(lines[: limit + 1])  # header line + limit data lines

    intent = {**_REPORT_INTENTS.get(report_type, {"query_type": "unknown"})}
    if limit is not None:
        intent["limit"] = limit
    live_rows = await execute(intent, conn, agency_id) or []

    return ReportResponse(
        report_type=snap["report_type"],
        rendered_at=snap["rendered_at"],
        text=text,
        rows=live_rows,
    )
