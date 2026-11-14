"""Ask tab — natural-language questions answered by the tools in
:data:`pipeline.query.tools.TOOLS`.

A question the deterministic router can match runs its tool directly; the
rest go to the LLM in tool-use mode over the same tools. Out-of-scope
questions get a friendly text refusal with 2–3 supported suggestions.

The request body carries the global :class:`~api.range.RangeCtx`
(time range / DOW / time-band) so the LLM and tools can scope to the
user's chosen window without having to mention it in the prompt.
"""

import json
import logging
from typing import Any

import asyncpg
import clickhouse_connect
from clickhouse_connect.driver.asyncclient import AsyncClient
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field, field_serializer, field_validator

from api.deps import get_agency, get_ch, get_current_user_optional, get_locale, get_pooled_conn
from api.middleware.ratelimit import limiter, tier_limit
from api.range import (
    RangeCtx,
    clamp_range_ctx,
    ctx_payload,
)
from api.security import User, csrf_guard
from pipeline.flags import aflag
from pipeline.query.chat import _chat_str, ch_failure_str, chat_with_tools
from pipeline.query.followup import MAX_QUESTION_CHARS
from pipeline.query.intent import _TOOL_DEFAULTS as _PAGINATABLE_TOOL_DEFAULTS
from pipeline.query.query_log import log_query
from pipeline.query.router import is_follow_up, needs_history, needs_previous_turn, route_or_examples
from pipeline.query.tools import _summary, dispatch, render_tool_result

_log = logging.getLogger(__name__)

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


# Turns actually attached to the follow-up prompt. Anything the caller sends
# beyond a small multiple of this is work the server will throw away.
HISTORY_TURNS_USED = 3
MAX_HISTORY_TURNS = 20
_MAX_TURN_ARGS_BYTES = 8192


class Turn(BaseModel):
    question: str = Field(max_length=MAX_QUESTION_CHARS)
    tool: str | None = None
    args: dict | None = None

    @field_validator("args")
    @classmethod
    def _bounded_args(cls, v: dict | None) -> dict | None:
        if v is not None and len(json.dumps(v).encode()) > _MAX_TURN_ARGS_BYTES:
            raise ValueError(f"args exceeds {_MAX_TURN_ARGS_BYTES} bytes serialized")
        return v


class AskRequest(BaseModel):
    question: str = Field(max_length=MAX_QUESTION_CHARS)
    ctx: AskCtx | None = None
    history: list[Turn] = Field(default_factory=list, max_length=MAX_HISTORY_TURNS)


class AskResponse(BaseModel):
    """Response schema for ``POST /ask``.

    Phase ② fields (``signature_hash``, ``confidence``, ``canonical_args``,
    ``cache_outcome``) are populated only when the ``ask_intent_cache_enabled``
    flag is on (``pipeline.flags``: a DB override, else ``ASK_INTENT_CACHE_ENABLED``)
    and the request went through the LLM Stage-3 path.  They are ``None`` on the
    Stage-1 / Stage-2 router paths and when the flag is off.
    """

    answer: str
    tool_call: dict | None = None
    result: dict | None = None
    ctx: dict
    router_stage: str | None = None
    signature_hash: str | None = None
    confidence: float | None = None
    canonical_args: dict | None = None
    cache_outcome: str | None = None

    @field_serializer("result")
    def _result_as_stored(self, result: dict | None) -> Any:
        # Pydantic dumps a Decimal inside an untyped dict as a string. FastAPI's
        # encoder sends it as a number, as the conversation store keeps it, so
        # an anonymous thread, which keeps this payload, charts it too.
        return jsonable_encoder(result)


def _resolve_ctx(body_ctx: AskCtx | None) -> RangeCtx:
    """Build a validated, clamped RangeCtx from the request body.

    An omitted ``ctx`` means "no opinion" and gets the default window;
    anything the client did send goes through the same validation as a query
    string (:func:`api.range.clamp_range_ctx`), so a malformed date or an
    unrecognised enum is a 422 rather than a silently different answer.
    """
    if body_ctx is None:
        return clamp_range_ctx(from_=None, to=None)
    return clamp_range_ctx(
        from_=body_ctx.from_date,
        to=body_ctx.to_date,
        dow=body_ctx.dow,
        time_band=body_ctx.time_band,
        service=body_ctx.service,
        routes=body_ctx.routes or (),
    )


@router.post("/ask", response_model=AskResponse)
@limiter.limit(tier_limit)
async def ask(
    request: Request,
    body: AskRequest,
    agency_id: int = Depends(get_agency),
    # Per-statement borrowing: the LLM wait below can last tens of seconds and
    # must not pin one of the pool's connections while it does.
    conn: asyncpg.Connection = Depends(get_pooled_conn),
    ch: AsyncClient = Depends(get_ch),
    locale: str = Depends(get_locale),
    user: User | None = Depends(get_current_user_optional),
) -> AskResponse:
    """Answer a natural-language question via tool-use.

    Cross-origin POSTs are rejected by ``csrf_guard`` before the LLM call
    fires, so an attacker can't burn the operator's LLM quota or extract
    answers through a victim's session cookie. The Accept-Language header
    picks the response locale (defaults to JP).
    """
    csrf_guard(request)
    # The frontend disables submit on empty input, but a direct API caller
    # could still POST an empty/whitespace question and get a misleading
    # describe_data answer back. Reject early.
    if not body.question or not body.question.strip():
        raise HTTPException(status_code=400, detail="question must not be empty")
    ctx = _resolve_ctx(body.ctx)

    ctx_dict = ctx_payload(ctx)

    history_enabled = await aflag("ask_history_enabled")
    log_enabled = await aflag("ask_query_log_enabled")
    router_enabled = await aflag("ask_router_enabled")

    # With history attached, a follow-up ("次の50件", "もっと") or a reference
    # back to the conversation ("その路線は？") skips Stages 1-2: a stateless
    # rule or embedding match cannot see the turn it continues or points at,
    # so it goes straight to the LLM with the last few turns.
    # Slice before dumping: the rest is discarded, so serializing it first is
    # work spent on data that never leaves here.
    history = [t.model_dump() for t in body.history[-HISTORY_TURNS_USED:]] if history_enabled else []
    follow_up = history_enabled and bool(history) and is_follow_up(body.question)
    skip_router = follow_up or (history_enabled and bool(history) and needs_history(body.question))
    # A forced tool call only makes sense when there is an actual prior tool
    # invocation to continue (e.g. re-paginating the same describe_data
    # call). is_follow_up()'s regex also matches ordinary follow-up
    # phrasing ("さらに", "それを", "他には") that can legitimately follow a
    # free-text answer (e.g. an out-of-scope refusal, history[-1]["tool"] is
    # None) — forcing a tool call there would remove the model's only
    # correct move (decline again) and risk a hallucinated call instead.
    # Scoped further to tools that actually support a continuation axis
    # (i.e. take an ``offset``, per ``_TOOL_DEFAULTS`` — currently only
    # ``describe_data``): a prior turn's tool without one (e.g.
    # ``on_time_rate``) has no valid re-invocation for a bare "もっと", so
    # forcing tool_choice="required" there would risk a nonsensical re-call
    # instead of a legitimate prose answer.
    _prior_tool = history[-1].get("tool") if follow_up else None
    force_tool_call = bool(_prior_tool) and "offset" in _PAGINATABLE_TOOL_DEFAULTS.get(_prior_tool or "", {})

    # Follow-up phrasing ("もっと", "次の50件", a bare "降順") with NO prior
    # result to continue: short-circuit to a gentle prompt. Otherwise the
    # question falls to the open LLM with no examples, which hallucinates a
    # page the user never asked for (e.g. describe_data(routes, offset=100)).
    # needs_previous_turn() lets through the ones a first turn can answer: a
    # refinement that names its own subject, or one a Stage-1 rule answers.
    if history_enabled and not history and needs_previous_turn(body.question, router_runs=router_enabled):
        msg = _summary("follow_up_no_history", lang=locale)
        resp = AskResponse(answer=msg, tool_call=None, result=None, ctx=ctx_dict, router_stage="no_history")
        if log_enabled:
            await log_query(conn, agency_id, body.question, "no_history", None, False)
        return resp

    # Single embed+search: dispatch decision and few-shot examples share
    # one embedding so the fall-through path doesn't re-embed the question.
    decision, examples = (None, [])
    if router_enabled and not skip_router:
        decision, examples = await route_or_examples(body.question, conn, agency_id, k=3)

    # Cache-layer telemetry — populated only on the Stage-3 (LLM) path.
    sig_hash: str | None = None
    cache_outcome: str | None = None
    # Numeric-hallucination-guard verdict, as chat_with_tools reports it. No
    # /ask path runs the guard, so it stays None (see log_query).
    numeric_guard_triggered: bool | None = None

    if decision is not None:
        stage = decision.stage
        tool_name = decision.tool
        # Stage 1/2 is the deterministic-router path (regex/embedding-matched
        # questions, e.g. describe_data's date_range/sample_counts/overview
        # kinds) — the ROUTINE path for those question kinds, not an edge
        # case. It has no LLM fallback to catch a failure, unlike Stage 3
        # (pipeline.query.chat.chat_with_tools), which already wraps every
        # dispatch(...) call in try/except and degrades to a tool_error
        # answer. Without a guard here, a ClickHouse-unavailable
        # HTTPException raised deep inside dispatch (e.g. via api.deps.get_ch's
        # _ClickHouseUnavailable stand-in) would 503 the whole /ask request
        # instead of returning a graceful 200 answer.
        #
        # The catch here is intentionally narrow — only the two ClickHouse
        # failure modes below degrade gracefully. Everything else (notably
        # asyncpg.exceptions.UndefinedTableError on a migration-lagged
        # environment) must propagate so FastAPI's registered exception
        # handlers (e.g. aggregate_not_ready_handler in api/main.py) can turn
        # it into their specific machine-readable response instead of a
        # generic 200 tool_error that masks it (see api/aggregate_errors.py).
        try:
            result = await dispatch(decision.tool, decision.args, ctx, conn, agency_id, locale=locale, ch=ch)
        except HTTPException as exc:
            # The only HTTPException dispatch is expected to raise here is the
            # _ClickHouseUnavailable stand-in's 503 (api/deps.py) — client was
            # never created at startup. Any other status code is a real error
            # (e.g. a tool's own 4xx) that must propagate untouched.
            if exc.status_code != 503:
                raise
            _log.warning("Tool %s unavailable (stage %s dispatch): ClickHouse client unavailable", decision.tool, stage)
            success = False
            resp = AskResponse(
                answer=_chat_str("service_unavailable", locale, name=decision.tool),
                tool_call={"name": decision.tool, "arguments": decision.args},
                result=None,
                ctx=ctx_dict,
                router_stage=stage,
            )
        except clickhouse_connect.driver.exceptions.Error as exc:
            # A real ClickHouse client was created, but the query itself
            # failed mid-flight (network blip, server error, etc.) — distinct
            # from the stand-in above, but the same graceful degrade applies.
            _log.exception("Tool %s failed (stage %s dispatch): ClickHouse query error", decision.tool, stage)
            success = False
            resp = AskResponse(
                answer=ch_failure_str(exc, locale, name=decision.tool),
                tool_call={"name": decision.tool, "arguments": decision.args},
                result=None,
                ctx=ctx_dict,
                router_stage=stage,
            )
        else:
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
            locale=locale,
            rag_examples=examples,
            history=history,
            ch=ch,
            force_tool_call=force_tool_call,
            user_id=user.user_id if user is not None else None,
            llm_approved=user.llm_approved if user is not None else False,
        )
        stage = "llm"
        tool_name = (payload.get("tool_call") or {}).get("name")
        success = payload["success"]
        sig_hash = payload.get("signature_hash")
        cache_outcome = payload.get("cache_outcome")
        numeric_guard_triggered = payload.get("numeric_guard_triggered")
        resp = AskResponse(
            answer=payload["answer"],
            tool_call=payload["tool_call"],
            result=payload["result"],
            ctx=ctx_dict,
            router_stage=stage,
            signature_hash=sig_hash,
            confidence=payload.get("confidence"),
            canonical_args=payload.get("canonical_args"),
            cache_outcome=cache_outcome,
        )

    if log_enabled:
        # Build-mode synthetic questions (``__build__ tool {...}`` from the guided
        # form) are machine-generated; logging the raw sentinel would pollute the
        # analytics view of what users actually ask. Skip logging for those.
        if not body.question.startswith("__build__"):
            await log_query(
                conn,
                agency_id,
                body.question,
                stage,
                tool_name,
                success,
                signature_hash=sig_hash,
                cache_outcome=cache_outcome,
                numeric_guard_triggered=numeric_guard_triggered,
            )

    return resp
