"""Endpoints for ChatGPT/Claude-style thread persistence: CRUD + append-message + anon migration."""

from __future__ import annotations

import json
import logging
from typing import Any

import asyncpg
import clickhouse_connect
from clickhouse_connect.driver.asyncclient import AsyncClient
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from api.deps import get_agency, get_ch, get_conn, get_current_user, get_current_user_optional, get_locale
from api.middleware.ratelimit import FREE_LIMIT, PRO_LIMIT, limiter
from api.range import RangeCtx, clamp_range_ctx
from api.security import User, csrf_guard, require_llm_approved
from pipeline.query import conversations as _conv
from pipeline.query import followup as _followup
from pipeline.query import intent_cache as _intent_cache
from pipeline.query.chat import _chat_str
from pipeline.query.followup_context import select_context_row
from pipeline.query.intent import IntentSignature, canonicalize, signature_hash
from pipeline.query.tools import dispatch, render_tool_result

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/{agency_id}", tags=["conversations"])


async def _owned_or_404(coro: Any) -> Any:
    """Await ``coro``, masking PermissionDenied/LookupError as a 404 so a
    caller can't distinguish "not owned" from "doesn't exist"."""
    try:
        return await coro
    except (_conv.PermissionDenied, LookupError):
        raise HTTPException(status_code=404, detail="not found") from None


def _ctx_from_stored_filters(filter_ctx: dict | None) -> RangeCtx:
    """Rebuild a conversation's saved filters into a validated RangeCtx.

    ``filter_ctx`` is client input that happens to have been persisted: the
    client chose every value in it, and the row can be replayed long after
    the code that wrote it changed. It therefore gets the same validation as
    a live query string (422 on a malformed date or an unknown enum) instead
    of being trusted — feeding it straight into ``RangeCtx`` turned a bad
    stored date into an unhandled ValueError (500) and let an arbitrary
    ``dow``/``time_band``/``service`` string reach the SQL builders, where an
    unbounded or unrecognised filter means a full scan rather than an error.
    """
    fc = filter_ctx or {}
    return clamp_range_ctx(
        from_=fc.get("from_date"),
        to=fc.get("to_date"),
        dow=fc.get("dow") or "all",
        time_band=fc.get("time_band") or "all",
        service=fc.get("service") or "all",
        routes=fc.get("routes") or (),
    )


def _raise_for_followup_error(err: str | None) -> None:
    """Map a ``pipeline.query.followup.answer_followup`` error code to the
    matching HTTP error, shared by both the anon and authed followup paths.

    ``too_long``/``empty`` are client input-validation failures (400), not
    provider/LLM failures (502) -- the frontend's ``canSubmit`` guard means
    ``empty`` should never reach here from the real UI, but a direct API
    call must still get a client-error status, not "bad gateway".
    ``not_approved`` is an authorization failure (403), not a provider
    outage -- retrying won't help until an admin flips the flag."""
    if err == "too_long":
        raise HTTPException(status_code=400, detail="question_too_long")
    if err == "empty":
        raise HTTPException(status_code=400, detail="question_empty")
    if err == "not_approved":
        raise HTTPException(status_code=403, detail="llm_not_approved")
    if err is not None:
        raise HTTPException(status_code=502, detail=f"llm_error:{err}")


class CreateConversation(BaseModel):
    title: str = Field(..., max_length=200)
    filter_ctx: dict[str, Any] = Field(default_factory=dict)


class UpdateConversation(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    pinned: bool | None = None
    filter_ctx: dict[str, Any] | None = None


class AppendMessage(BaseModel):
    # Chip dispatch is gone; the only dispatch path is (tool + args). The field
    # is still parsed so that a client still sending one gets the explicit 410
    # below instead of a misleading "tool is required" 400.
    chip_id: str | None = None
    args_override: dict[str, Any] | None = None
    # Supported dispatch path: builder direct dispatch (tool + args)
    tool: str | None = None
    args: dict[str, Any] | None = None
    # Optional client-supplied user-bubble label so the chat doesn't show raw
    # ``metric=avg_delay`` strings. The frontend (which owns the i18n maps)
    # computes a localized summary and sends it through. Server-generated
    # fallback exists only when this is omitted.
    user_summary: str | None = None

    def validate_dispatch(self) -> None:
        """Require exactly one dispatch path to be named.

        A lone ``chip_id`` passes here and is answered with a 410 by the
        endpoint, so a retired client learns the path is gone rather than
        that its arguments were malformed.
        """
        has_chip = bool(self.chip_id)
        has_tool_args = bool(self.tool) and self.args is not None
        if has_chip and has_tool_args:
            raise ValueError("Provide either chip_id or (tool + args), not both")
        if not has_chip and not has_tool_args:
            raise ValueError("One of chip_id or (tool + args) is required")


# Mirrors the preset range_ctx ceiling: the same filter state, arriving by a
# different route. Bounded per thread so a 100-thread migration cannot carry
# an unbounded jsonb payload into Postgres.
_MAX_FILTER_CTX_BYTES = 64 * 1024


class AnonThread(BaseModel):
    client_id: str
    # The agency the thread belongs to; threads span agencies in localStorage,
    # so each is homed under its own agency (None → fall back to request scope).
    agency_id: int | None = None
    title: str = Field(max_length=200)
    filter_ctx: dict[str, Any] = Field(default_factory=dict)

    @field_validator("filter_ctx")
    @classmethod
    def _bounded_filter_ctx(cls, v: dict[str, Any]) -> dict[str, Any]:
        if len(json.dumps(v).encode()) > _MAX_FILTER_CTX_BYTES:
            raise ValueError(f"filter_ctx exceeds {_MAX_FILTER_CTX_BYTES} bytes serialized")
        return v

    pinned: bool = False
    created_at: str
    updated_at: str
    messages: list[dict[str, Any]] = Field(default_factory=list, max_length=500)


class MigrateAnon(BaseModel):
    threads: list[AnonThread] = Field(max_length=100)


@router.get("/conversations", response_model=None)
async def list_conversations(
    agency_id: int = Depends(get_agency),  # implicit auth scope
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_conn),
) -> list[dict[str, Any]]:
    """Return the caller's 50 most recent conversations for this agency."""
    rows = await _conv.list_conversations(conn, user_id=user.user_id, agency_id=agency_id, limit=50)
    return rows


@router.post("/conversations", response_model=None)
@limiter.limit(f"{FREE_LIMIT};{PRO_LIMIT}")
async def create_conversation(
    request: Request,
    body: CreateConversation,
    agency_id: int = Depends(get_agency),  # implicit auth scope
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    """Create a conversation owned by the caller with the given title + filter_ctx."""
    csrf_guard(request)
    return await _conv.create_conversation(
        conn,
        user_id=user.user_id,
        agency_id=agency_id,
        title=body.title,
        filter_ctx=body.filter_ctx,
    )


@router.get("/conversations/{conversation_id}", response_model=None)
async def get_conversation(
    conversation_id: str,
    agency_id: int = Depends(get_agency),  # implicit auth scope
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    """Return one conversation with its messages; 404 unless the caller owns it."""
    return await _owned_or_404(_conv.get_conversation(conn, conversation_id, user_id=user.user_id, agency_id=agency_id))


@router.patch("/conversations/{conversation_id}", response_model=None)
@limiter.limit(f"{FREE_LIMIT};{PRO_LIMIT}")
async def update_conversation(
    request: Request,
    conversation_id: str,
    body: UpdateConversation,
    agency_id: int = Depends(get_agency),  # implicit auth scope
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    """Patch title / pinned / filter_ctx on a conversation the caller owns."""
    csrf_guard(request)
    fields = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    return await _owned_or_404(
        _conv.update_conversation(conn, conversation_id, user_id=user.user_id, agency_id=agency_id, **fields)
    )


@router.delete("/conversations/{conversation_id}")
@limiter.limit(f"{FREE_LIMIT};{PRO_LIMIT}")
async def delete_conversation(
    request: Request,
    conversation_id: str,
    agency_id: int = Depends(get_agency),  # implicit auth scope
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_conn),
) -> dict[str, bool]:
    """Delete a conversation the caller owns (messages cascade)."""
    csrf_guard(request)
    await _owned_or_404(_conv.delete_conversation(conn, conversation_id, user_id=user.user_id, agency_id=agency_id))
    return {"ok": True}


@router.get("/conversations/{conversation_id}/messages", response_model=None)
async def list_messages(
    conversation_id: str,
    agency_id: int = Depends(get_agency),  # implicit auth scope
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_conn),
) -> list[dict[str, Any]]:
    """Return all messages of a conversation the caller owns."""
    return await _owned_or_404(_conv.list_messages(conn, conversation_id, user_id=user.user_id, agency_id=agency_id))


@router.post("/conversations/migrate-anon")
@limiter.limit(f"{FREE_LIMIT};{PRO_LIMIT}")
async def migrate_anon_endpoint(
    request: Request,
    body: MigrateAnon,
    agency_id: int = Depends(get_agency),  # implicit auth scope
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_conn),
) -> dict[str, int]:
    """Import anonymous localStorage threads into the caller's account."""
    csrf_guard(request)
    threads = [t.model_dump() for t in body.threads]
    inserted = await _conv.migrate_anon_threads(
        conn,
        user_id=user.user_id,
        agency_id=agency_id,
        threads=threads,
    )
    return {"inserted": inserted}


@router.post("/conversations/{conversation_id}/messages", response_model=None)
@limiter.limit(f"{FREE_LIMIT};{PRO_LIMIT}")
async def append_message_endpoint(
    request: Request,
    conversation_id: str,
    body: AppendMessage,
    agency_id: int = Depends(get_agency),  # implicit auth scope
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_conn),
    ch: AsyncClient = Depends(get_ch),
    locale: str = Depends(get_locale),
) -> dict[str, dict[str, Any]]:
    """Dispatch a {tool, args} question and persist user + assistant rows atomically."""
    csrf_guard(request)
    # Validate dispatch path before touching DB.
    try:
        body.validate_dispatch()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Ownership check up front. The remainder of the endpoint runs in a single
    # transaction so a concurrent DELETE of this conversation between the
    # ownership check and the message inserts can't produce a 500 (the FK
    # cascade would otherwise tear out the rows we're trying to write).
    conv = await _owned_or_404(_conv.get_conversation(conn, conversation_id, user_id=user.user_id, agency_id=agency_id))

    # ── Resolve tool + args (builder-direct path only) ────────────────────────
    if body.chip_id is not None:
        raise HTTPException(
            status_code=410,
            detail="chip dispatch is no longer supported; use {tool, args} instead",
        )

    # Builder direct — tool and args supplied by client.
    # validate_dispatch() already rejected tool=None; narrow for the type checker.
    if body.tool is None:
        raise HTTPException(status_code=400, detail="tool is required")
    resolved_tool = body.tool
    resolved_args = body.args or {}
    # Prefer the client-supplied localized summary; fall back to a generic
    # label that does NOT expose raw key=value pairs (those leak English/
    # identifier noise into the JA chat bubble).
    user_summary = body.user_summary or f"🛠 {resolved_tool}"

    ctx_obj = _ctx_from_stored_filters(conv["filter_ctx"])
    ctx_dict = {"from_date": ctx_obj.from_date, "to_date": ctx_obj.to_date}

    # Wrap user-msg + cache upsert + dispatch + assistant-msg in one transaction.
    # Without it, a concurrent DELETE of this conversation would FK-cascade-delete
    # the user_msg row mid-flight, producing a 500 instead of a clean 404.
    async with conn.transaction():
        user_msg = await _conv.append_message(
            conn,
            conversation_id,
            role="user",
            tool=None,
            args=None,
            signature_hash=None,
            result=None,
            rendered_summary=user_summary,
        )

        # Canonicalize + cache upsert (always bumps hit_count for chip/builder paths)
        try:
            can_args = canonicalize(resolved_tool, resolved_args, ctx_dict)
        except ValueError:
            can_args = dict(resolved_args)
        sig_hash = signature_hash(resolved_tool, can_args)
        try:
            # Nested transaction (asyncpg emits a SAVEPOINT here since we're
            # already inside conn.transaction()). Any Postgres error raised by
            # dispatch() below — not just the UndefinedTableError case — would
            # otherwise abort the OUTER transaction, poisoning `conn` so the
            # append_message() calls in the except branches below raise
            # asyncpg.exceptions.InFailedSQLTransactionError instead of writing
            # the intended graceful tool_error/service_unavailable message.
            # The savepoint confines that abort to the upsert+dispatch scope,
            # leaving the outer transaction (holding user_msg) writable.
            async with conn.transaction():
                await _intent_cache.upsert(
                    conn,
                    sig_hash,
                    IntentSignature(tool=resolved_tool, args=resolved_args, confidence=1.0),
                    can_args,
                    agency_id,
                    question=user_summary,
                )
                result = await dispatch(resolved_tool, can_args, ctx_obj, conn, agency_id, locale=locale, ch=ch)
        except HTTPException as exc:
            if exc.status_code != 503:
                raise
            _log.warning("Tool %s unavailable: %s", resolved_tool, exc.detail)
            rendered = _chat_str("service_unavailable", locale, name=resolved_tool)
            assistant_msg = await _conv.append_message(
                conn,
                conversation_id,
                role="assistant",
                tool=resolved_tool,
                args=can_args,
                signature_hash=sig_hash,
                result=None,
                rendered_summary=rendered,
            )
            return {"user": user_msg, "assistant": assistant_msg}
        except clickhouse_connect.driver.exceptions.Error:
            _log.exception("Tool %s failed: ClickHouse query error", resolved_tool)
            rendered = _chat_str("service_unavailable", locale, name=resolved_tool)
            assistant_msg = await _conv.append_message(
                conn,
                conversation_id,
                role="assistant",
                tool=resolved_tool,
                args=can_args,
                signature_hash=sig_hash,
                result=None,
                rendered_summary=rendered,
            )
            return {"user": user_msg, "assistant": assistant_msg}
        except asyncpg.exceptions.UndefinedTableError:
            # A missing agg_* table (migration/analyze behind) must propagate to
            # FastAPI's registered aggregate_not_ready_handler (api/main.py +
            # api/aggregate_errors.py) so the frontend gets the machine-readable
            # {"code": "aggregate_not_ready"} 503 it reacts to — not a generic
            # tool_error that masks it (mirrors api/routers/ask.py's Fix-8f
            # convention). Re-raising immediately, before any further query runs
            # on this `conn`, also avoids poisoning the still-open
            # `conn.transaction()` above with a second failing statement (which
            # would otherwise surface as an unhandled
            # asyncpg.exceptions.InFailedSQLTransactionError instead of this
            # error) — the transaction context manager rolls back cleanly once
            # this propagates out of it.
            raise
        except Exception:
            # Never interpolate raw exception text into a persisted message —
            # it can carry internal details (SQL fragments, relation names,
            # endpoint URLs) that would resurface every time this conversation
            # is reloaded. Full detail still goes to the server-side log.
            _log.exception("Tool %s failed", resolved_tool)
            rendered = _chat_str("tool_error", locale, name=resolved_tool)
            assistant_msg = await _conv.append_message(
                conn,
                conversation_id,
                role="assistant",
                tool=resolved_tool,
                args=can_args,
                signature_hash=sig_hash,
                result=None,
                rendered_summary=rendered,
            )
            return {"user": user_msg, "assistant": assistant_msg}

        rendered = render_tool_result(result, locale=locale)
        result_dict = {
            "kind": result.kind,
            "summary": result.summary,
            "rows": result.rows,
            "columns": result.columns,
            "series": result.series,
            "pairs": result.pairs,
        }
        assistant_msg = await _conv.append_message(
            conn,
            conversation_id,
            role="assistant",
            tool=resolved_tool,
            args=can_args,
            signature_hash=sig_hash,
            result=result_dict,
            rendered_summary=rendered,
            conditions={"dow": ctx_obj.dow, "time_band": ctx_obj.time_band, "service": ctx_obj.service},
        )
    return {"user": user_msg, "assistant": assistant_msg}


# ─── LLM follow-up (kill-switch gated) ────────────────────────────────────────


class FollowupBody(BaseModel):
    # No max_length here on purpose: a Pydantic-level length violation would
    # 422 before this handler ever runs `answer_followup`'s own `len(q) >
    # MAX_QUESTION_CHARS` check, so `_raise_for_followup_error`'s friendly
    # "question_too_long" 400 (and its matching frontend copy) would never
    # actually fire for a real oversized question -- only for the mocked
    # unit test that calls `answer_followup` directly.
    question: str = Field(...)
    # Grounding context is always read from the referenced assistant message
    # in the DB, never inlined by the client: this endpoint requires a
    # signed-in, admin-approved caller, so the prior turn is always stored
    # server-side. A missing reference is rejected in the handler rather than
    # by a validator here, so the 400 carries the endpoint's own message.
    context_message_id: int | None = None
    context_row_index: int | None = Field(default=None, ge=0, strict=True)


@router.post("/conversations/{conversation_id}/followup", response_model=None)
@limiter.limit(f"{FREE_LIMIT};{PRO_LIMIT}")
async def followup_endpoint(
    request: Request,
    conversation_id: str,
    body: FollowupBody,
    agency_id: int = Depends(get_agency),  # implicit auth scope
    user: User | None = Depends(get_current_user_optional),
    locale: str = Depends(get_locale),
) -> dict[str, dict[str, Any]]:
    """LLM-grounded follow-up on a prior assistant result.

    Disabled by default; flip ``ASK_FOLLOWUP_ENABLED=true`` to enable. The
    follow-up calls the LLM with the prior message's structured result as the
    sole grounding context — no tool dispatch, no external retrieval.

    Auth: requires a signed-in, admin-approved caller (``users.llm_approved``).
    Anonymous callers never have a ``users.llm_approved`` row, so they're
    rejected before the LLM is touched — there is no anonymous path here.
    ``context_message_id`` must point to a DB-stored assistant message in one
    of the caller's own conversations.
    """
    csrf_guard(request)

    if not _followup.is_enabled():
        # Short-circuit ahead of the approval gate: a disabled feature must
        # not 403 an unapproved caller before reporting itself as off.
        raise HTTPException(status_code=503, detail="followup_disabled")
    # Called directly (not via Depends) on the already-resolved `user` so it
    # runs after the kill-switch check above, not before it -- FastAPI
    # resolves Depends() params before the endpoint body, which would
    # reverse that precedence.
    user = require_llm_approved(user)

    if body.context_message_id is None:
        raise HTTPException(
            status_code=400,
            detail="authed followup requires context_message_id",
        )

    # Two short-lived connections acquired around the LLM call, rather than
    # one held across it via ``Depends(get_conn)``: ``answer_followup`` waits
    # on a provider for seconds, and a pool connection parked for that long
    # is one no other request can use. The reads here and the writes below
    # share no transaction — the writes open their own — so nothing needs a
    # single connection to span both.
    async with request.app.state.pool.acquire() as conn:
        # Ownership check (also confirms the conversation exists).
        await _owned_or_404(_conv.get_conversation(conn, conversation_id, user_id=user.user_id, agency_id=agency_id))

        # Fetch the context message directly (must belong to this conversation).
        try:
            ctx_msg = await _conv.get_message(
                conn, conversation_id, body.context_message_id, user_id=user.user_id, agency_id=agency_id
            )
        except (_conv.PermissionDenied, LookupError):
            raise HTTPException(status_code=404, detail="context message not found") from None

    if ctx_msg.get("role") != "assistant":
        raise HTTPException(status_code=400, detail="context must be an assistant message")

    answer, err = await _followup.answer_followup(
        question=body.question,
        context_tool=ctx_msg.get("tool"),
        context_args=ctx_msg.get("args"),
        context_result=select_context_row(ctx_msg.get("result"), body.context_row_index),
        locale=locale,
        llm_approved=user.llm_approved,
    )
    _raise_for_followup_error(err)

    # Append both messages atomically so a mid-flight cancel doesn't leave
    # a dangling user message in the thread.
    async with request.app.state.pool.acquire() as conn:
        async with conn.transaction():
            user_msg = await _conv.append_message(
                conn,
                conversation_id,
                role="user",
                tool=None,
                args=None,
                signature_hash=None,
                result=None,
                rendered_summary=body.question,
            )
            assistant_msg = await _conv.append_message(
                conn,
                conversation_id,
                role="assistant",
                tool=None,
                args={"context_message_id": body.context_message_id, "context_row_index": body.context_row_index},
                signature_hash=None,
                result=None,
                rendered_summary=answer,
            )
    return {"user": user_msg, "assistant": assistant_msg}


@router.get("/ask/followup-enabled", response_model=None)
async def followup_enabled_endpoint(
    agency_id: int = Depends(get_agency),  # implicit auth scope
) -> dict[str, Any]:
    """Public flag check so the frontend knows whether to render the input.

    Also exposes ``max_question_chars`` so the client's input cap can't drift
    from :data:`pipeline.query.followup.MAX_QUESTION_CHARS`."""
    return {
        "enabled": _followup.is_enabled(),
        "max_question_chars": _followup.MAX_QUESTION_CHARS,
    }
