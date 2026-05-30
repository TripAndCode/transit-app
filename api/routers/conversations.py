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
from pipeline.query.chip_catalog import CHIPS_BY_ID
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
    chip_id: str | None = None
    args_override: dict[str, Any] | None = None


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
        conn, user_id=user.user_id, agency_id=agency_id,
        title=body.title, filter_ctx=body.filter_ctx,
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


# NOTE: migrate-anon must be declared BEFORE the {conversation_id} routes so
# FastAPI doesn't try to match "migrate-anon" as a conversation_id UUID.
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
        conn, user_id=user.user_id, agency_id=agency_id, threads=threads,
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
    # Ownership check up front.
    try:
        conv = await _conv.get_conversation(conn, conversation_id, user_id=user.user_id)
    except (_conv.PermissionDenied, LookupError):
        raise HTTPException(status_code=404, detail="not found") from None

    # Resolve chip — 400 for missing or unknown chip_id
    if not body.chip_id or body.chip_id not in CHIPS_BY_ID:
        raise HTTPException(status_code=400, detail=f"unknown chip_id: {body.chip_id!r}")
    chip = CHIPS_BY_ID[body.chip_id]
    args = {**chip.args, **(body.args_override or {})}

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

    # Append the user message first.
    user_msg = await _conv.append_message(
        conn, conversation_id, role="user", chip_id=chip.id,
        tool=None, args=None, signature_hash=None, result=None,
        rendered_summary=chip.title_ja if locale == "ja" else chip.title_en,
    )

    # Dispatch the tool — canonical signature + cache upsert (always bump)
    try:
        can_args = canonicalize(chip.tool, args, ctx_dict)
    except ValueError:
        can_args = dict(args)
    sig_hash = signature_hash(chip.tool, can_args)
    try:
        await _intent_cache.upsert(
            conn, sig_hash,
            IntentSignature(tool=chip.tool, args=args, confidence=1.0),
            can_args, agency_id, question=chip.title_ja,
        )
        result = await dispatch(chip.tool, can_args, ctx_obj, conn, agency_id, locale=locale)
    except Exception as exc:
        rendered = f"ツール {chip.tool} の実行に失敗しました: {exc}"
        assistant_msg = await _conv.append_message(
            conn, conversation_id, role="assistant", chip_id=chip.id,
            tool=chip.tool, args=can_args, signature_hash=sig_hash,
            result=None, rendered_summary=rendered,
        )
        return {"user": user_msg, "assistant": assistant_msg}

    rendered = render_tool_result(result, locale=locale)
    result_dict = {
        "kind": result.kind, "summary": result.summary, "rows": result.rows,
        "columns": result.columns, "series": result.series, "pairs": result.pairs,
    }
    assistant_msg = await _conv.append_message(
        conn, conversation_id, role="assistant", chip_id=chip.id,
        tool=chip.tool, args=can_args, signature_hash=sig_hash,
        result=result_dict, rendered_summary=rendered,
    )
    return {"user": user_msg, "assistant": assistant_msg}
