"""Endpoints for ChatGPT/Claude-style thread persistence: CRUD + append-message + anon migration."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from api.deps import get_agency, get_conn, get_current_user, get_locale
from api.middleware.ratelimit import FREE_LIMIT, PRO_LIMIT, limiter
from api.range import RangeCtx
from api.security import csrf_guard
from pipeline.query import conversations as _conv
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
    agency_id: int = Depends(get_agency),
    user=Depends(get_current_user),
    conn=Depends(get_conn),
):
    rows = await _conv.list_conversations(conn, user_id=user.user_id, agency_id=agency_id, limit=50)
    return rows


@router.post("/conversations")
@limiter.limit(f"{FREE_LIMIT};{PRO_LIMIT}")
async def create_conversation(
    request: Request,
    body: CreateConversation,
    agency_id: int = Depends(get_agency),
    user=Depends(get_current_user),
    conn=Depends(get_conn),
):
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
    agency_id: int = Depends(get_agency),  # noqa: ARG001 — implicit auth scope
    user=Depends(get_current_user),
    conn=Depends(get_conn),
):
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
    agency_id: int = Depends(get_agency),  # noqa: ARG001
    user=Depends(get_current_user),
    conn=Depends(get_conn),
):
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
    agency_id: int = Depends(get_agency),  # noqa: ARG001
    user=Depends(get_current_user),
    conn=Depends(get_conn),
):
    csrf_guard(request)
    try:
        await _conv.delete_conversation(conn, conversation_id, user_id=user.user_id)
    except (_conv.PermissionDenied, LookupError):
        raise HTTPException(status_code=404, detail="not found") from None
    return {"ok": True}


@router.get("/conversations/{conversation_id}/messages")
async def list_messages(
    conversation_id: str,
    agency_id: int = Depends(get_agency),  # noqa: ARG001
    user=Depends(get_current_user),
    conn=Depends(get_conn),
):
    try:
        return await _conv.list_messages(conn, conversation_id, user_id=user.user_id)
    except (_conv.PermissionDenied, LookupError):
        raise HTTPException(status_code=404, detail="not found") from None


@router.post("/conversations/migrate-anon")
@limiter.limit(f"{FREE_LIMIT};{PRO_LIMIT}")
async def migrate_anon_endpoint(
    request: Request,
    body: MigrateAnon,
    agency_id: int = Depends(get_agency),
    user=Depends(get_current_user),
    conn=Depends(get_conn),
):
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
    agency_id: int = Depends(get_agency),
    user=Depends(get_current_user),
    conn=Depends(get_conn),
    locale: str = Depends(get_locale),
):
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

    # Builder direct — tool and args supplied by client
    resolved_tool = body.tool  # type: ignore[assignment]  # validated above
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
