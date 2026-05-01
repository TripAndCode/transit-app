from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from api.deps import get_conn, get_agency
from api.middleware.ratelimit import limiter, FREE_LIMIT, PRO_LIMIT
from pipeline.query.intent import classify_intent
from pipeline.query.executor import execute
from pipeline.query.formatter import format_result, format_unknown

router = APIRouter(prefix="/api/{agency_id}", tags=["ask"])


class AskRequest(BaseModel):
    question: str
    model: str = "llama-3.2-11b-text-preview"


class AskResponse(BaseModel):
    answer: str
    intent: dict


@router.post("/ask", response_model=AskResponse)
@limiter.limit(f"{FREE_LIMIT};{PRO_LIMIT}")
async def ask(
    request: Request,
    body: AskRequest,
    agency_id: int = Depends(get_agency),
    conn=Depends(get_conn),
):
    intent = await classify_intent(body.question, model=body.model)
    if intent.get("unknown"):
        answer = await format_unknown(body.question, conn, agency_id, model=body.model)
    else:
        rows = await execute(intent, conn, agency_id)
        answer = format_result(intent["query_type"], rows, intent)
    return AskResponse(answer=answer, intent=intent)
