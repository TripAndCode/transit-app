"""Ask tab — natural-language Japanese questions answered via tool-use.

v1 used a single intent classifier that fell off a cliff for anything
outside ~15 known templates. v2 uses Groq's tool-use mode against the
six tools defined in :mod:`pipeline.query.tools`. Out-of-scope questions
get a friendly text refusal with 2–3 supported suggestions.

The request body now carries the global :class:`~api.range.RangeCtx`
(time range / DOW / time-band) so the LLM and tools can scope to the
user's chosen window without having to mention it in the prompt.
"""

import asyncio
import logging
import os as _os
from datetime import date, timedelta
from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from api.deps import get_agency, get_conn, get_locale
from api.middleware.ratelimit import FREE_LIMIT, PRO_LIMIT, limiter
from api.range import DEFAULT_RANGE_DAYS, MAX_RANGE_DAYS, DowFilter, RangeCtx, ServiceType, TimeBand, parse_iso_date
from api.security import csrf_guard
from pipeline.query import intent_cache as _intent_cache
from pipeline.query.chat import chat_with_tools
from pipeline.query.embeddings import get_embedder
from pipeline.query.query_log import log_query
from pipeline.query.rag_index import nearest as rag_nearest
from pipeline.query.router import _load_golden, is_follow_up, route_or_examples
from pipeline.query.tools import dispatch, render_tool_result

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
    """Response schema for ``POST /ask``.

    Phase ② fields (``signature_hash``, ``confidence``, ``canonical_args``,
    ``cache_outcome``) are populated only when ``ASK_INTENT_CACHE_ENABLED=true``
    and the request went through the LLM Stage-3 path.  They are ``None`` on the
    Stage-1 / Stage-2 router paths and when the flag is off.
    """

    answer: str
    tool_call: dict | None = None
    result: dict | None = None
    ctx: dict
    router_stage: str | None = None
    # Phase ② canonical-intent cache fields
    signature_hash: str | None = None
    confidence: float | None = None
    canonical_args: dict | None = None
    cache_outcome: str | None = None


def _resolve_ctx(body_ctx: AskCtx | None) -> RangeCtx:
    """Build a clamped RangeCtx from the request body, defaulting invalid enums to 'all'."""
    today = date.today()
    if body_ctx is None:
        return RangeCtx(from_date=today - timedelta(days=DEFAULT_RANGE_DAYS - 1), to_date=today)

    to_date = parse_iso_date(body_ctx.to_date) or today
    from_date = parse_iso_date(body_ctx.from_date) or (to_date - timedelta(days=DEFAULT_RANGE_DAYS - 1))
    if from_date > to_date:
        from_date, to_date = to_date, from_date
    if (to_date - from_date).days >= MAX_RANGE_DAYS:
        from_date = to_date - timedelta(days=MAX_RANGE_DAYS - 1)

    dow = cast(DowFilter, body_ctx.dow if body_ctx.dow in ("all", "weekday", "weekend") else "all")
    valid_bands = {"all", "morning", "forenoon", "noon", "afternoon", "evening", "night", "late_night"}
    tb = cast(TimeBand, body_ctx.time_band if body_ctx.time_band in valid_bands else "all")
    svc = cast(ServiceType, body_ctx.service if body_ctx.service in ("all", "平日", "土日祝") else "all")
    routes = tuple(r for r in (body_ctx.routes or []) if r)[:100]

    return RangeCtx(
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
    # The frontend disables submit on empty input, but a direct API caller
    # could still POST an empty/whitespace question and get a misleading
    # describe_data answer back. Reject early.
    if not body.question or not body.question.strip():
        raise HTTPException(status_code=400, detail="question must not be empty")
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
            )

    return resp


# ---------------------------------------------------------------------------
# Build-schema metadata
# ---------------------------------------------------------------------------

# Tools that are useful in the guided builder UI.  capabilities and route_meta
# are excluded: capabilities is a discovery tool, route_meta requires a route
# arg that isn't meaningful as a standalone builder form.
_BUILD_TOOL_NAMES = ("top_n", "time_series", "compare_segments", "route_stats", "describe_data")

# Per-tool labels (ja / en) and field override metadata for the builder UI.
# Enum options and defaults are derived from _TOOL_DEFAULTS where possible;
# this dict supplies the human-readable labels and the enum option lists that
# aren't captured in _TOOL_DEFAULTS.
# NOTE: ``service_type`` and ``time_window`` are intentionally absent from every
# tool's builder fields. Those overlap with the per-thread FilterContextBar
# (期間 ・ 曜日 ・ 時間帯), which is the single source of truth for time + DOW
# scope. Chips MAY pre-set these as args (overriding filter context for that
# specific dispatch); the builder UI does not duplicate them.
_BUILD_TOOL_META: dict[str, dict[str, Any]] = {
    "top_n": {
        "label_ja": "ランキング",
        "label_en": "Ranking",
        "fields": [
            {
                "key": "metric",
                "type": "enum",
                "options": ["avg_delay", "on_time_rate", "worst_5min"],
            },
            {"key": "n", "type": "int", "min": 1, "max": 50, "default": 10},
            {"key": "best_first", "type": "bool", "default": False},
        ],
    },
    "time_series": {
        "label_ja": "トレンド",
        "label_en": "Trend",
        "fields": [
            {"key": "route", "type": "string", "optional": True},
            {
                "key": "granularity",
                "type": "enum",
                "options": ["day", "week", "month"],
                "default": "day",
            },
        ],
    },
    "compare_segments": {
        "label_ja": "セグメント比較",
        "label_en": "Segment Comparison",
        "fields": [
            {"key": "route", "type": "string", "optional": True},
            {
                "key": "dimension",
                "type": "enum",
                "options": ["dow", "service_type"],
                "default": "dow",
            },
        ],
    },
    "route_stats": {
        "label_ja": "路線統計",
        "label_en": "Route Stats",
        "fields": [
            {"key": "route", "type": "string"},
        ],
    },
    "describe_data": {
        "label_ja": "データ照会",
        "label_en": "Data Info",
        "fields": [
            {
                "key": "kind",
                "type": "enum",
                "options": [
                    "routes",
                    "stops",
                    "date_range",
                    "agencies",
                    "sample_counts",
                    "overview",
                    "metrics",
                ],
            },
            {"key": "limit", "type": "int", "min": 1, "max": 200, "default": 50},
        ],
    },
}


@router.get("/ask/build-schema")
async def ask_build_schema(
    request: Request,
    agency_id: int = Depends(get_agency),
    locale: str = Depends(get_locale),
):
    """Return tool-form metadata for the frontend's guided build mode.

    Driven by ``_BUILD_TOOL_META`` + ``_TOOL_DEFAULTS``. The
    ``capabilities`` and ``route_meta`` tools are excluded as they are
    not useful in a builder.
    """
    tools_out = []
    for name in _BUILD_TOOL_NAMES:
        meta = _BUILD_TOOL_META.get(name, {})
        entry: dict[str, Any] = {
            "name": name,
            "label_ja": meta.get("label_ja", name),
            "label_en": meta.get("label_en", name),
            "fields": meta.get("fields", []),
        }
        tools_out.append(entry)

    return {"tools": tools_out}


# ---------------------------------------------------------------------------
# Suggest endpoint
# ---------------------------------------------------------------------------


@router.get("/ask/suggest")
@limiter.limit(f"{FREE_LIMIT};{PRO_LIMIT}")
async def ask_suggest(
    request: Request,
    agency_id: int = Depends(get_agency),
    conn=Depends(get_conn),
    q: str = Query(default=""),
    limit: int = Query(default=8),
):
    """Live autocomplete for the Ask input.

    With a non-empty ``q``: e5-embed the query, nearest-neighbour against
    ``rag_chunks``, return up to ``limit`` (max 12) results ordered by
    ascending cosine distance.

    With an empty ``q``: return top-N most-hit chunks as a starter chip
    set, ordered by ``hit_count DESC``.
    """
    # Clamp limit to [1, 12] even if FastAPI validation lets something through.
    limit = max(1, min(12, limit))

    if not q or not q.strip():
        # Empty query: return top-N most-hit chunks for this agency.
        rows = await conn.fetch(
            """
            SELECT rc.chunk_id, rc.content
            FROM rag_chunks rc
            LEFT JOIN ask_intent_cache aic
              ON aic.last_question = rc.content
             AND aic.agency_id = rc.agency_id
            WHERE rc.agency_id = $1
            ORDER BY aic.hit_count DESC NULLS LAST, rc.chunk_id
            LIMIT $2
            """,
            agency_id,
            limit,
        )
        golden = _load_golden()
        result = []
        for row in rows:
            cid = row["chunk_id"]
            tool, args = golden.get(cid, ("", {}))
            result.append(
                {
                    "question": row["content"],
                    "tool": tool,
                    "args": dict(args),
                    "distance": None,
                }
            )
        return result

    # Non-empty query: embed + NN search.
    embedder = get_embedder()
    if not getattr(embedder, "available", False):
        return []

    try:
        qvec = await asyncio.to_thread(embedder.embed, q.strip(), mode="query")
    except Exception:
        _log.debug("ask_suggest: embedding failed; returning no suggestions", exc_info=True)
        return []

    try:
        matches = await rag_nearest(conn, agency_id, qvec, k=limit)
    except Exception:
        _log.debug("ask_suggest: rag_nearest failed; returning no suggestions", exc_info=True)
        return []

    golden = _load_golden()
    result = []
    for m in matches:
        tool, args = golden.get(m.chunk_id, ("", {}))
        result.append(
            {
                "question": m.content,
                "tool": tool,
                "args": dict(args),
                "distance": m.distance,
            }
        )
    return result


# ---------------------------------------------------------------------------
# Edit-action endpoint
# ---------------------------------------------------------------------------


class EditActionRequest(BaseModel):
    signature_hash: str
    action: str


@router.post("/ask/edit-action")
@limiter.limit(f"{FREE_LIMIT};{PRO_LIMIT}")
async def ask_edit_action(
    request: Request,
    body: EditActionRequest,
    agency_id: int = Depends(get_agency),
    conn=Depends(get_conn),
):
    """Record the user's verdict on a cached interpretation.

    Body: ``{"signature_hash": str, "action": "confirmed"|"edited"}``
    Returns ``{"ok": true}`` on success, 400 on unknown action.

    Same CSRF + rate-limit guards as ``POST /ask`` — without them a cross-
    origin attacker could mark arbitrary cache rows as ``edited`` (blocking
    promotion) or ``confirmed`` (rubber-stamping bad interpretations).
    """
    csrf_guard(request)
    try:
        await _intent_cache.update_user_action(conn, body.signature_hash, agency_id, body.action)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}
