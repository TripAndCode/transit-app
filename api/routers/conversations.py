"""Endpoints for ChatGPT/Claude-style thread persistence: CRUD + append-message + anon migration."""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, model_validator

from api.deps import get_agency, get_conn, get_current_user, get_current_user_optional, get_locale
from api.middleware.ratelimit import FREE_LIMIT, PRO_LIMIT, limiter
from api.range import RangeCtx
from api.security import csrf_guard
from pipeline.query import conversations as _conv
from pipeline.query import followup as _followup
from pipeline.query import intent_cache as _intent_cache
from pipeline.query.intent import IntentSignature, canonicalize, signature_hash
from pipeline.query.tools import dispatch, render_tool_result

router = APIRouter(prefix="/api/{agency_id}", tags=["conversations"])


class CreateConversation(BaseModel):
    title: str = Field(..., max_length=200)
    filter_ctx: dict[str, Any] = Field(default_factory=dict)


class UpdateConversation(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    pinned: bool | None = None
    filter_ctx: dict[str, Any] | None = None


class AppendMessage(BaseModel):
    # chip_id is retained for API compatibility but triggers a 410 in the endpoint.
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
        """Validate that (tool + args) is supplied.

        chip_id is accepted at parse time but causes a 410 in the endpoint.
        Providing both chip_id and tool+args is still a 400.
        """
        has_chip = bool(self.chip_id)
        has_tool_args = bool(self.tool) and self.args is not None
        if has_chip and has_tool_args:
            raise ValueError("Provide either chip_id or (tool + args), not both")
        if not has_chip and not has_tool_args:
            raise ValueError("One of chip_id or (tool + args) is required")


class AnonThread(BaseModel):
    client_id: str
    # The agency the thread belongs to; threads span agencies in localStorage,
    # so each is homed under its own agency (None → fall back to request scope).
    agency_id: int | None = None
    title: str
    filter_ctx: dict[str, Any] = Field(default_factory=dict)
    pinned: bool = False
    created_at: str
    updated_at: str
    messages: list[dict[str, Any]] = Field(default_factory=list)


class MigrateAnon(BaseModel):
    threads: list[AnonThread]


@router.get("/conversations")
async def list_conversations(
    agency_id: int = Depends(get_agency),  # implicit auth scope
    user=Depends(get_current_user),
    conn=Depends(get_conn),
):
    """Return the caller's 50 most recent conversations for this agency."""
    rows = await _conv.list_conversations(conn, user_id=user.user_id, agency_id=agency_id, limit=50)
    return rows


@router.post("/conversations")
@limiter.limit(f"{FREE_LIMIT};{PRO_LIMIT}")
async def create_conversation(
    request: Request,
    body: CreateConversation,
    agency_id: int = Depends(get_agency),  # implicit auth scope
    user=Depends(get_current_user),
    conn=Depends(get_conn),
):
    """Create a conversation owned by the caller with the given title + filter_ctx."""
    csrf_guard(request)
    return await _conv.create_conversation(
        conn,
        user_id=user.user_id,
        agency_id=agency_id,
        title=body.title,
        filter_ctx=body.filter_ctx,
    )


@router.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: str,
    agency_id: int = Depends(get_agency),  # implicit auth scope
    user=Depends(get_current_user),
    conn=Depends(get_conn),
):
    """Return one conversation with its messages; 404 unless the caller owns it."""
    try:
        return await _conv.get_conversation(conn, conversation_id, user_id=user.user_id)
    except (_conv.PermissionDenied, LookupError):
        # Mask non-owned threads as 404 (don't reveal existence)
        raise HTTPException(status_code=404, detail="not found") from None


@router.patch("/conversations/{conversation_id}")
@limiter.limit(f"{FREE_LIMIT};{PRO_LIMIT}")
async def update_conversation(
    request: Request,
    conversation_id: str,
    body: UpdateConversation,
    agency_id: int = Depends(get_agency),  # implicit auth scope
    user=Depends(get_current_user),
    conn=Depends(get_conn),
):
    """Patch title / pinned / filter_ctx on a conversation the caller owns."""
    csrf_guard(request)
    fields = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    try:
        return await _conv.update_conversation(conn, conversation_id, user_id=user.user_id, **fields)
    except (_conv.PermissionDenied, LookupError):
        raise HTTPException(status_code=404, detail="not found") from None


@router.delete("/conversations/{conversation_id}")
@limiter.limit(f"{FREE_LIMIT};{PRO_LIMIT}")
async def delete_conversation(
    request: Request,
    conversation_id: str,
    agency_id: int = Depends(get_agency),  # implicit auth scope
    user=Depends(get_current_user),
    conn=Depends(get_conn),
):
    """Delete a conversation the caller owns (messages cascade)."""
    csrf_guard(request)
    try:
        await _conv.delete_conversation(conn, conversation_id, user_id=user.user_id)
    except (_conv.PermissionDenied, LookupError):
        raise HTTPException(status_code=404, detail="not found") from None
    return {"ok": True}


@router.get("/conversations/{conversation_id}/messages")
async def list_messages(
    conversation_id: str,
    agency_id: int = Depends(get_agency),  # implicit auth scope
    user=Depends(get_current_user),
    conn=Depends(get_conn),
):
    """Return all messages of a conversation the caller owns."""
    try:
        return await _conv.list_messages(conn, conversation_id, user_id=user.user_id)
    except (_conv.PermissionDenied, LookupError):
        raise HTTPException(status_code=404, detail="not found") from None


@router.post("/conversations/migrate-anon")
@limiter.limit(f"{FREE_LIMIT};{PRO_LIMIT}")
async def migrate_anon_endpoint(
    request: Request,
    body: MigrateAnon,
    agency_id: int = Depends(get_agency),  # implicit auth scope
    user=Depends(get_current_user),
    conn=Depends(get_conn),
):
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


@router.post("/conversations/{conversation_id}/messages")
@limiter.limit(f"{FREE_LIMIT};{PRO_LIMIT}")
async def append_message_endpoint(
    request: Request,
    conversation_id: str,
    body: AppendMessage,
    agency_id: int = Depends(get_agency),  # implicit auth scope
    user=Depends(get_current_user),
    conn=Depends(get_conn),
    locale: str = Depends(get_locale),
):
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
    try:
        conv = await _conv.get_conversation(conn, conversation_id, user_id=user.user_id)
    except (_conv.PermissionDenied, LookupError):
        raise HTTPException(status_code=404, detail="not found") from None

    # ── Resolve tool + args (builder-direct path only) ────────────────────────
    if body.chip_id is not None:
        # chip dispatch was removed in Phase ③.5; use {tool, args} instead.
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
    resolved_chip_id: str | None = None
    # Prefer the client-supplied localized summary; fall back to a generic
    # label that does NOT expose raw key=value pairs (those leak English/
    # identifier noise into the JA chat bubble).
    user_summary = body.user_summary or f"🛠 {resolved_tool}"

    # Build a RangeCtx from the conversation's filter_ctx
    fc = conv["filter_ctx"] or {}
    today = date.today()
    ctx_obj = RangeCtx(
        from_date=date.fromisoformat(fc["from_date"]) if fc.get("from_date") else today - timedelta(days=29),
        to_date=date.fromisoformat(fc["to_date"]) if fc.get("to_date") else today,
        dow=fc.get("dow", "all"),
        time_band=fc.get("time_band", "all"),
        service=fc.get("service", "all"),
        routes=tuple(fc.get("routes") or ()),
    )
    ctx_dict = {"from_date": ctx_obj.from_date, "to_date": ctx_obj.to_date}

    # Wrap user-msg + cache upsert + dispatch + assistant-msg in one transaction.
    # Without it, a concurrent DELETE of this conversation would FK-cascade-delete
    # the user_msg row mid-flight, producing a 500 instead of a clean 404.
    async with conn.transaction():
        user_msg = await _conv.append_message(
            conn,
            conversation_id,
            role="user",
            chip_id=resolved_chip_id,
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
            await _intent_cache.upsert(
                conn,
                sig_hash,
                IntentSignature(tool=resolved_tool, args=resolved_args, confidence=1.0),
                can_args,
                agency_id,
                question=user_summary,
            )
            result = await dispatch(resolved_tool, can_args, ctx_obj, conn, agency_id, locale=locale)
        except Exception as exc:
            rendered = f"ツール {resolved_tool} の実行に失敗しました: {exc}"
            assistant_msg = await _conv.append_message(
                conn,
                conversation_id,
                role="assistant",
                chip_id=resolved_chip_id,
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
            chip_id=resolved_chip_id,
            tool=resolved_tool,
            args=can_args,
            signature_hash=sig_hash,
            result=result_dict,
            rendered_summary=rendered,
        )
    return {"user": user_msg, "assistant": assistant_msg}


# ─── LLM follow-up (kill-switch gated) ────────────────────────────────────────


class FollowupBody(BaseModel):
    question: str = Field(..., max_length=_followup.MAX_QUESTION_CHARS)
    # Authed path: reference an existing assistant message stored in DB
    context_message_id: int | None = None
    # Anon path: inline the prior result (frontend has it in localStorage)
    context_tool: str | None = None
    context_args: dict[str, Any] | None = None
    context_result: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _validate_context(self) -> "FollowupBody":
        has_db_ref = self.context_message_id is not None
        has_inline = self.context_result is not None
        if has_db_ref and has_inline:
            raise ValueError("Provide either context_message_id or inline context, not both")
        # Neither is valid but we let the endpoint decide based on auth — anon
        # without inline context will 400 in the handler.
        return self


@router.post("/conversations/{conversation_id}/followup")
@limiter.limit(f"{FREE_LIMIT};{PRO_LIMIT}")
async def followup_endpoint(
    request: Request,
    conversation_id: str,
    body: FollowupBody,
    agency_id: int = Depends(get_agency),  # implicit auth scope
    user=Depends(get_current_user_optional),
    conn=Depends(get_conn),
    locale: str = Depends(get_locale),
):
    """LLM-grounded follow-up on a prior assistant result.

    Disabled by default; flip ``ASK_FOLLOWUP_ENABLED=true`` to enable. The
    follow-up calls the LLM with the prior message's structured result as the
    sole grounding context — no tool dispatch, no external retrieval.

    Auth: accepts both authenticated and anonymous callers.
    - Authed: context_message_id points to a DB-stored assistant message.
    - Anon: context_tool/context_args/context_result inlined in request body
      (the frontend holds these in localStorage).
    """
    csrf_guard(request)

    if not _followup.is_enabled():
        raise HTTPException(status_code=503, detail="followup_disabled")

    if user is None:
        # ── Anon path ─────────────────────────────────────────────────────────
        if body.context_result is None:
            raise HTTPException(
                status_code=400,
                detail="anon followup requires inline context (context_result)",
            )

        answer, err = await _followup.answer_followup(
            question=body.question,
            context_tool=body.context_tool,
            context_args=body.context_args,
            context_result=body.context_result,
            locale=locale,
        )
        if err == "too_long":
            raise HTTPException(status_code=400, detail="question_too_long")
        if err is not None:
            raise HTTPException(status_code=502, detail=f"llm_error:{err}")

        # Return synthetic messages — frontend persists them to localStorage.
        now = datetime.now(tz=timezone.utc).isoformat()
        base_id = -int(time.time() * 1000)
        return {
            "user": {
                "message_id": base_id,
                "conversation_id": conversation_id,
                "role": "user",
                "chip_id": None,
                "tool": None,
                "args": None,
                "signature_hash": None,
                "result": None,
                "rendered_summary": body.question,
                "created_at": now,
            },
            "assistant": {
                "message_id": base_id - 1,
                "conversation_id": conversation_id,
                "role": "assistant",
                "chip_id": None,
                "tool": None,
                "args": {"context_message_id": body.context_message_id},
                "signature_hash": None,
                "result": None,
                "rendered_summary": answer,
                "created_at": now,
            },
        }

    # ── Authed path ───────────────────────────────────────────────────────────
    if body.context_message_id is None:
        raise HTTPException(
            status_code=400,
            detail="authed followup requires context_message_id",
        )

    # Ownership check (also confirms the conversation exists).
    try:
        await _conv.get_conversation(conn, conversation_id, user_id=user.user_id)
    except (_conv.PermissionDenied, LookupError):
        raise HTTPException(status_code=404, detail="not found") from None

    # Fetch the context message (must belong to this conversation).
    messages = await _conv.list_messages(conn, conversation_id, user_id=user.user_id)
    ctx_msg = next(
        (m for m in messages if m["message_id"] == body.context_message_id),
        None,
    )
    if ctx_msg is None:
        raise HTTPException(status_code=404, detail="context message not found")
    if ctx_msg.get("role") != "assistant":
        raise HTTPException(status_code=400, detail="context must be an assistant message")

    answer, err = await _followup.answer_followup(
        question=body.question,
        context_tool=ctx_msg.get("tool"),
        context_args=ctx_msg.get("args"),
        context_result=ctx_msg.get("result"),
        locale=locale,
    )
    if err == "too_long":
        raise HTTPException(status_code=400, detail="question_too_long")
    if err is not None:
        raise HTTPException(status_code=502, detail=f"llm_error:{err}")

    # Append both messages atomically so a mid-flight cancel doesn't leave
    # a dangling user message in the thread.
    async with conn.transaction():
        user_msg = await _conv.append_message(
            conn,
            conversation_id,
            role="user",
            chip_id=None,
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
            chip_id=None,
            tool=None,
            args={"context_message_id": body.context_message_id},
            signature_hash=None,
            result=None,
            rendered_summary=answer,
        )
    return {"user": user_msg, "assistant": assistant_msg}


@router.get("/ask/followup-enabled")
async def followup_enabled_endpoint(
    agency_id: int = Depends(get_agency),  # implicit auth scope
):
    """Public flag check so the frontend knows whether to render the input."""
    return {"enabled": _followup.is_enabled()}
