"""Copilot proactive-insight endpoint.

See ``pipeline.query.copilot`` for the template-selection-only LLM call this
wraps: it renders a canned template filled with numbers pulled from the
caller's own view payload, never free-form user text, which is why this
route needs no RAG grounding or answer verification unlike ``/ask``.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from api.deps import get_agency, get_current_user_optional, get_locale
from api.middleware.ratelimit import (
    FREE_LIMIT,
    PRO_LIMIT,
    AnonCopilotQuotaExceeded,
    anon_ip_key,
    check_and_consume_anon_quota,
    copilot_anon_daily_limit,
    copilot_anon_ip_daily_limit,
    get_or_issue_anon_session,
    limiter,
)
from api.security import csrf_guard
from pipeline.query.copilot import NoInsightAvailable, generate_proactive_insight

router = APIRouter(prefix="/api/{agency_id}", tags=["copilot"])


class CopilotInsightRequest(BaseModel):
    tab: str
    filters: dict
    view_payload: dict


class CopilotInsightResponse(BaseModel):
    text: str
    cite: str
    low_confidence: bool


@router.post("/copilot/insight", response_model=CopilotInsightResponse)
@limiter.limit(f"{FREE_LIMIT};{PRO_LIMIT}")
async def copilot_insight(
    request: Request,
    response: Response,
    body: CopilotInsightRequest,
    agency_id: int = Depends(get_agency),
    locale: str = Depends(get_locale),
    user=Depends(get_current_user_optional),
):
    csrf_guard(request)
    if user is None:
        session_key = get_or_issue_anon_session(request, response)
        ip_key = anon_ip_key(request)
        if not check_and_consume_anon_quota(
            session_key,
            ip_key,
            scope="copilot",
            daily_limit=copilot_anon_daily_limit(),
            ip_daily_limit=copilot_anon_ip_daily_limit(),
        ):
            raise AnonCopilotQuotaExceeded()

    try:
        result = await generate_proactive_insight(body.tab, body.filters, body.view_payload, locale=locale)
    except NoInsightAvailable as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return CopilotInsightResponse(**result)
