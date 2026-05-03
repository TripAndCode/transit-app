"""Reports endpoints — list available report types and serve live queries.

The reports table layer used to be a pre-rendered ``snapshots`` table written
by ``make analyze``. v2 replaces that with on-demand queries against the
``agg_*`` aggregate tables so reports can honor the user's time-range filter.
``rendered_at`` in the response is the moment the request was served.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from api.deps import get_agency, get_conn
from api.middleware.ratelimit import FREE_LIMIT, PRO_LIMIT, limiter
from pipeline.query.executor import execute
from pipeline.query.formatter import format_result

router = APIRouter(prefix="/api/{agency_id}", tags=["reports"])

# Each report key maps to a base intent dict; the executor + formatter handle
# the actual SQL and Japanese rendering. Order here is the listing order.
_REPORT_INTENTS: dict[str, dict] = {
    "ranking": {"query_type": "ranking", "limit": 100},
    "ranking_best": {"query_type": "ranking", "limit": 100, "sort_order": "asc"},
    "on_time": {"query_type": "on_time", "limit": 100},
    "worst_5min": {"query_type": "worst_5min", "limit": 100},
    "trend": {"query_type": "trend"},
    "compare_ranking": {"query_type": "compare_ranking", "limit": 100},
    "dow_weekend": {"query_type": "dow_ranking", "dow_group": "weekend", "limit": 100},
    "dow_weekday": {"query_type": "dow_ranking", "dow_group": "weekday", "limit": 100},
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
    """Static list of available report types. ``rendered_at`` is request time.

    The ``conn`` dependency is unused here but kept for parity with
    ``get_report`` and to ensure the agency_id check still runs.
    """
    del conn  # explicitly unused
    now = datetime.now(timezone.utc)
    return [{"report_type": rt, "rendered_at": now} for rt in _REPORT_INTENTS]


@router.get("/reports/{report_type}", response_model=ReportResponse)
@limiter.limit(f"{FREE_LIMIT};{PRO_LIMIT}")
async def get_report(
    request: Request,
    report_type: str,
    limit: int | None = Query(default=None, ge=1),
    agency_id: int = Depends(get_agency),
    conn=Depends(get_conn),
):
    """Run the named report live against the aggregate tables."""
    if report_type not in _REPORT_INTENTS:
        raise HTTPException(status_code=404, detail=f"Unknown report type '{report_type}'")

    intent = {**_REPORT_INTENTS[report_type]}
    if limit is not None:
        intent["limit"] = limit

    rows = await execute(intent, conn, agency_id) or []
    text = format_result(intent["query_type"], rows, intent)

    return ReportResponse(
        report_type=report_type,
        rendered_at=datetime.now(timezone.utc),
        text=text,
        rows=rows,
    )
