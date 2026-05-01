from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.deps import get_conn, get_agency
from pipeline.query.executor import execute
from pipeline.query.formatter import format_result

router = APIRouter(prefix="/api/{agency_id}", tags=["query"])


class QueryRequest(BaseModel):
    query_type: str
    unknown: bool = False
    route: str | None = None
    route_name: str | None = None
    service: str | None = None
    dow: str | None = None
    dow_group: str | None = None
    date: str | None = None
    stop_name: str | None = None
    time_band: str | None = None
    trend_direction: str = "any"
    compare_polarity: str = "any"
    sort_order: str = "desc"
    limit: int = 15
    by_hour: bool = False
    by_dow: bool = False
    by_stop: bool = False
    compare: bool = False


class QueryResponse(BaseModel):
    answer: str
    rows: list


@router.post("/query", response_model=QueryResponse)
async def query(
    body: QueryRequest,
    agency_id: int = Depends(get_agency),
    conn=Depends(get_conn),
):
    intent = body.model_dump()
    rows = await execute(intent, conn, agency_id) or []
    answer = format_result(intent["query_type"], rows, intent)
    return QueryResponse(answer=answer, rows=rows)
