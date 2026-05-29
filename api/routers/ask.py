"""Ask tab — natural-language Japanese questions answered via tool-use.

v1 used a single intent classifier that fell off a cliff for anything
outside ~15 known templates. v2 uses Groq's tool-use mode against the
six tools defined in :mod:`pipeline.query.tools`. Out-of-scope questions
get a friendly text refusal with 2–3 supported suggestions.

The request body now carries the global :class:`~api.range.RangeCtx`
(time range / DOW / time-band) so the LLM and tools can scope to the
user's chosen window without having to mention it in the prompt.
"""

import os as _os
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from api.deps import get_agency, get_conn, get_locale
from api.middleware.ratelimit import FREE_LIMIT, PRO_LIMIT, limiter
from api.range import DEFAULT_RANGE_DAYS, MAX_RANGE_DAYS, RangeCtx, parse_iso_date
from api.security import csrf_guard
from pipeline.query.chat import chat_with_tools
from pipeline.query.query_log import log_query
from pipeline.query.router import is_follow_up, route_or_examples
from pipeline.query.tools import dispatch, render_tool_result

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


class Turn(BaseModel):
    question: str
    tool: str | None = None
    args: dict | None = None


class AskRequest(BaseModel):
    question: str
    model: str | None = None
    ctx: AskCtx | None = None
    history: list[Turn] = []


class AskResponse(BaseModel):
    answer: str
    tool_call: dict | None = None
    result: dict | None = None
    ctx: dict
    router_stage: str | None = None


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

    ctx_dict = {
        "from": ctx.from_date.isoformat(),
        "to": ctx.to_date.isoformat(),
        "dow": ctx.dow,
        "time_band": ctx.time_band,
        "service": ctx.service,
        "routes": list(ctx.routes),
    }

    history_enabled = _os.environ.get("ASK_HISTORY_ENABLED", "true").lower() != "false"
    log_enabled = _os.environ.get("ASK_QUERY_LOG_ENABLED", "true").lower() != "false"
    router_enabled = _os.environ.get("ASK_ROUTER_ENABLED", "true").lower() != "false"

    # Follow-ups ("次の50件", "もっと") have no standalone tool mapping, so
    # they skip the stateless router and go straight to the LLM with the
    # last few turns attached. History is capped at 3 turns.
    history = [t.model_dump() for t in body.history][-3:] if history_enabled else []
    follow_up = history_enabled and bool(history) and is_follow_up(body.question)

    # Follow-up phrasing ("もっと", "次の50件") with NO prior result to
    # continue: short-circuit to a gentle prompt. Otherwise the question
    # falls to the open LLM with no examples, which hallucinates a page the
    # user never asked for (e.g. describe_data(routes, offset=100)).
    if history_enabled and not history and is_follow_up(body.question):
        msg = (
            "前の検索結果が見つかりませんでした。まず質問してから「もっと」「次の50件」などで続けてください。"
            if locale != "en"
            else "No previous result to continue. Ask a question first, then use 'more' / 'next 50' to page."
        )
        resp = AskResponse(answer=msg, tool_call=None, result=None, ctx=ctx_dict, router_stage="no_history")
        if log_enabled:
            await log_query(conn, agency_id, body.question, "no_history", None, False)
        return resp

    # Single embed+search: dispatch decision and few-shot examples share
    # one embedding so the fall-through path doesn't re-embed the question.
    decision, examples = (None, [])
    if router_enabled and not follow_up:
        decision, examples = await route_or_examples(body.question, conn, agency_id, k=3)

    # Cache-layer telemetry — populated only on the Stage-3 (LLM) path.
    sig_hash: str | None = None
    cache_outcome: str | None = None

    if decision is not None:
        result = await dispatch(decision.tool, decision.args, ctx, conn, agency_id, locale=locale)
        stage = decision.stage
        tool_name = decision.tool
        success = result.kind != "empty"
        resp = AskResponse(
            answer=render_tool_result(result, locale=locale),
            tool_call={"name": decision.tool, "arguments": decision.args},
            result={
                "kind": result.kind,
                "summary": result.summary,
                "rows": result.rows,
                "columns": result.columns,
                "series": result.series,
                "pairs": result.pairs,
            },
            ctx=ctx_dict,
            router_stage=stage,
        )
    else:
        payload = await chat_with_tools(
            body.question,
            ctx,
            conn,
            agency_id,
            model=body.model,
            locale=locale,
            rag_examples=examples,
            history=history,
        )
        stage = "llm"
        tool_name = (payload.get("tool_call") or {}).get("name")
        success = payload["success"]
        sig_hash = payload.get("signature_hash")
        cache_outcome = payload.get("cache_outcome")
        resp = AskResponse(
            answer=payload["answer"],
            tool_call=payload["tool_call"],
            result=payload["result"],
            ctx=ctx_dict,
            router_stage=stage,
        )

    if log_enabled:
        await log_query(
            conn,
            agency_id,
            body.question,
            stage,
            tool_name,
            success,
            signature_hash=sig_hash,
            cache_outcome=cache_outcome,
        )

    return resp
