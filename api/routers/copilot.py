"""Copilot proactive-insight endpoint.

See ``pipeline.query.copilot``: the insight is a canned template chosen and
filled in code from the caller's own view payload, never model output, so
this route needs no RAG grounding, answer verification, or LLM approval,
unlike ``/ask`` and the follow-up.
"""

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator

from api.deps import get_agency, get_locale
from api.middleware.ratelimit import limiter, tier_limit
from api.security import csrf_guard
from pipeline.query.copilot import NoInsightAvailable, ais_enabled, generate_proactive_insight

router = APIRouter(prefix="/api/{agency_id}", tags=["copilot"])

# A ceiling on abuse, not a tuning knob: CopilotPanel posts the whole
# OverviewSummary unmodified, and two of its fields (service_split_daily,
# sparkline_points) carry one entry per day of the selected range. At
# MAX_RANGE_DAYS those two alone run to tens of kilobytes, so any cap near
# their size rejects the application's own traffic on a wide-but-legal range.
# Keep this far above whatever the Overview tab can produce.
_MAX_PAYLOAD_BYTES = 256 * 1024


class CopilotInsightRequest(BaseModel):
    """Takes no filters: the insight is a function of the view payload alone,
    which already reflects them. A client that still sends ``filters`` is
    accepted and the field is ignored."""

    tab: str
    view_payload: dict

    @field_validator("view_payload")
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
@limiter.limit(tier_limit)
async def copilot_insight(
    request: Request,
    body: CopilotInsightRequest,
    agency_id: int = Depends(get_agency),
    locale: str = Depends(get_locale),
) -> CopilotInsightResponse:
    csrf_guard(request)
    if not await ais_enabled():
        # The panel hides itself off ``/copilot/enabled`` rather than relying
        # on this response.
        raise HTTPException(status_code=503, detail="copilot_disabled")
    try:
        result = await generate_proactive_insight(body.tab, body.view_payload, locale=locale)
    except NoInsightAvailable as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return CopilotInsightResponse(**result)


@router.get("/copilot/enabled")
async def copilot_enabled_endpoint(
    agency_id: int = Depends(get_agency),  # implicit auth scope
) -> dict[str, bool]:
    """Public flag check so the panel knows whether to render at all."""
    return {"enabled": await ais_enabled()}
