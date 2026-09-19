"""Copilot proactive-insight endpoint.

See ``pipeline.query.copilot`` for the template-selection-only LLM call this
wraps: it renders a canned template filled with numbers pulled from the
caller's own view payload, never free-form user text, which is why this
route needs no RAG grounding or answer verification unlike ``/ask``.
"""

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator

from api.deps import get_agency, get_current_user_optional, get_locale
from api.middleware.ratelimit import FREE_LIMIT, PRO_LIMIT, limiter
from api.security import csrf_guard, require_llm_approved
from pipeline.query.copilot import NoInsightAvailable, generate_proactive_insight, is_enabled
from pipeline.query.user_llm_keys import get_user_llm_key

router = APIRouter(prefix="/api/{agency_id}", tags=["copilot"])

_MAX_PAYLOAD_BYTES = 16384


class CopilotInsightRequest(BaseModel):
    tab: str
    filters: dict
    view_payload: dict

    @field_validator("filters", "view_payload")
    @classmethod
    def _bounded_payload(cls, v: dict) -> dict:
        size = len(json.dumps(v).encode())
        if size > _MAX_PAYLOAD_BYTES:
            raise ValueError(f"payload exceeds {_MAX_PAYLOAD_BYTES} bytes serialized")
        return v


class CopilotInsightResponse(BaseModel):
    text: str
    cite: str
    low_confidence: bool


@router.post("/copilot/insight", response_model=CopilotInsightResponse)
@limiter.limit(f"{FREE_LIMIT};{PRO_LIMIT}")
async def copilot_insight(
    request: Request,
    body: CopilotInsightRequest,
    agency_id: int = Depends(get_agency),
    locale: str = Depends(get_locale),
    user=Depends(get_current_user_optional),
):
    csrf_guard(request)
    if not is_enabled():
        # Short-circuit ahead of the approval gate: a disabled feature must
        # not 403 an unapproved caller, and the panel hides itself off the
        # ``/copilot/enabled`` flag rather than relying on this response.
        raise HTTPException(status_code=503, detail="copilot_disabled")
    # Called directly (not via Depends) on the already-resolved `user` so it
    # runs after the kill-switch check above, not before it -- FastAPI
    # resolves Depends() params before the endpoint body, which would
    # reverse that precedence.
    user = require_llm_approved(user)

    # Resolve the caller's BYOK key (if any) with a pool connection acquired
    # and released *before* the LLM call below — never held across it, which
    # can run for several seconds. This route otherwise has no reason to
    # touch Postgres at all (unlike ``/ask``, which already threads ``conn``
    # through for tool dispatch regardless of BYOK), so avoid declaring
    # ``conn=Depends(get_conn)`` for the whole handler, matching
    # ``api/routers/me.py``'s ``put_llm_key`` lazy-acquire pattern.
    async with request.app.state.pool.acquire() as conn:
        user_key = await get_user_llm_key(conn, user.user_id)

    try:
        result = await generate_proactive_insight(
            body.tab,
            body.filters,
            body.view_payload,
            locale=locale,
            user_key=user_key,
        )
    except NoInsightAvailable as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return CopilotInsightResponse(**result)


@router.get("/copilot/enabled")
async def copilot_enabled_endpoint(
    agency_id: int = Depends(get_agency),  # implicit auth scope
):
    """Public flag check so the panel knows whether to render at all."""
    return {"enabled": is_enabled()}
