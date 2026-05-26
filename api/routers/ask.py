"""Ask tab — natural-language Japanese questions answered via tool-use.

v1 used a single intent classifier that fell off a cliff for anything
outside ~15 known templates. v2 uses Groq's tool-use mode against the
six tools defined in :mod:`pipeline.query.tools`. Out-of-scope questions
get a friendly text refusal with 2–3 supported suggestions.

The request body now carries the global :class:`~api.range.RangeCtx`
(time range / DOW / time-band) so the LLM and tools can scope to the
user's chosen window without having to mention it in the prompt.
"""

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from api.deps import get_agency, get_conn, get_locale
from api.middleware.ratelimit import FREE_LIMIT, PRO_LIMIT, limiter
from api.range import DEFAULT_RANGE_DAYS, MAX_RANGE_DAYS, RangeCtx, parse_iso_date
from api.security import csrf_guard
from pipeline.query.chat import chat_with_tools

router = APIRouter(prefix="/api/{agency_id}", tags=["ask"])


class AskCtx(BaseModel):
    """Range context the frontend passes alongside the question."""

    from_date: str | None = Field(default=None, alias="from")
    to_date: str | None = Field(default=None, alias="to")
    dow: str = "all"
    time_band: str = "all"
    service: str = "all"
    routes: list[str] = []

    model_config = {"populate_by_name": True}


class AskRequest(BaseModel):
    question: str
    model: str | None = None
    ctx: AskCtx | None = None


class AskResponse(BaseModel):
    answer: str
    tool_call: dict | None = None
    result: dict | None = None
    ctx: dict


def _resolve_ctx(body_ctx: AskCtx | None) -> RangeCtx:
    today = date.today()
    if body_ctx is None:
        return RangeCtx(from_date=today - timedelta(days=DEFAULT_RANGE_DAYS - 1), to_date=today)

    to_date = parse_iso_date(body_ctx.to_date) or today
    from_date = parse_iso_date(body_ctx.from_date) or (to_date - timedelta(days=DEFAULT_RANGE_DAYS - 1))
    if from_date > to_date:
        from_date, to_date = to_date, from_date
    if (to_date - from_date).days >= MAX_RANGE_DAYS:
        from_date = to_date - timedelta(days=MAX_RANGE_DAYS - 1)

    dow = body_ctx.dow if body_ctx.dow in ("all", "weekday", "weekend") else "all"
    valid_bands = {"all", "morning", "forenoon", "noon", "afternoon", "evening", "night", "late_night"}
    tb = body_ctx.time_band if body_ctx.time_band in valid_bands else "all"
    svc = body_ctx.service if body_ctx.service in ("all", "平日", "土日祝") else "all"
    routes = tuple(r for r in (body_ctx.routes or []) if r)[:100]

    return RangeCtx(  # type: ignore[arg-type]
        from_date=from_date,
        to_date=to_date,
        dow=dow,
        time_band=tb,
        service=svc,
        routes=routes,
    )


@router.post("/ask", response_model=AskResponse)
@limiter.limit(f"{FREE_LIMIT};{PRO_LIMIT}")
async def ask(
    request: Request,
    body: AskRequest,
    agency_id: int = Depends(get_agency),
    conn=Depends(get_conn),
    locale: str = Depends(get_locale),
):
    """Answer a natural-language question via tool-use.

    Cross-origin POSTs are rejected by ``csrf_guard`` before the LLM call
    fires, so an attacker can't burn the operator's Groq quota or extract
    answers through a victim's session cookie. The Accept-Language header
    picks the response locale (defaults to JP).
    """
    csrf_guard(request)
    ctx = _resolve_ctx(body.ctx)
    payload = await chat_with_tools(body.question, ctx, conn, agency_id, model=body.model, locale=locale)
    return AskResponse(
        answer=payload["answer"],
        tool_call=payload["tool_call"],
        result=payload["result"],
        ctx={
            "from": ctx.from_date.isoformat(),
            "to": ctx.to_date.isoformat(),
            "dow": ctx.dow,
            "time_band": ctx.time_band,
            "service": ctx.service,
            "routes": list(ctx.routes),
        },
    )
