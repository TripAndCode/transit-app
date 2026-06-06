"""Self-service endpoints for logged-in users."""

import json
from typing import Any

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from api.deps import get_conn
from api.security import User, csrf_guard, require_user

router = APIRouter(prefix="/api", tags=["me"])


class IdentityOut(BaseModel):
    """One OAuth identity linked to the caller."""

    provider: str
    email_at_link: str | None


class MeOut(BaseModel):
    """Caller profile + linked identities returned by ``GET /api/me``."""

    user_id: int
    email: str
    name: str | None
    avatar_url: str | None
    role: str
    identities: list[IdentityOut]


@router.get("/me", response_model=MeOut)
async def get_me(user: User = Depends(require_user), conn: asyncpg.Connection = Depends(get_conn)):
    """Return the current user's profile and linked OAuth identities."""
    rows = await conn.fetch(
        "SELECT provider, email_at_link FROM oauth_identities WHERE user_id=$1",
        user.user_id,
    )
    return MeOut(
        user_id=user.user_id,
        email=user.email,
        name=user.name,
        avatar_url=user.avatar_url,
        role=user.role,
        identities=[IdentityOut(**dict(r)) for r in rows],
    )


class SessionOut(BaseModel):
    """One active session row exposed to the caller (sid truncated to a prefix)."""

    sid_prefix: str
    user_agent: str | None
    ip: str | None
    created_at: Any
    last_seen_at: Any


@router.get("/me/sessions", response_model=list[SessionOut])
async def list_sessions(user: User = Depends(require_user), conn=Depends(get_conn)):
    """List the caller's active sessions, ordered by most-recent activity."""
    rows = await conn.fetch(
        "SELECT sid, user_agent, ip::text AS ip, created_at, last_seen_at "
        "FROM sessions WHERE user_id=$1 ORDER BY last_seen_at DESC",
        user.user_id,
    )
    return [
        SessionOut(
            sid_prefix=r["sid"][:12],
            user_agent=r["user_agent"],
            ip=r["ip"],
            created_at=r["created_at"],
            last_seen_at=r["last_seen_at"],
        )
        for r in rows
    ]


@router.delete("/me/sessions/{sid_prefix}", status_code=204)
async def revoke_session(
    sid_prefix: str,
    request: Request,
    user: User = Depends(require_user),
    conn=Depends(get_conn),
):
    """Revoke the caller's session matching the given sid prefix.

    Rejects ambiguous prefixes with 409 — the UI passes the 12-char
    display prefix, which is astronomically unlikely to collide for
    opaque 32-byte tokens but the server refuses to guess if it does.
    """
    csrf_guard(request)
    if len(sid_prefix) < 12:
        raise HTTPException(400, "prefix too short")
    # secrets.token_urlsafe() only emits these characters; reject anything else
    # so a path containing `%` or `_` can't become a LIKE wildcard.
    if not all(c.isalnum() or c in "-_" for c in sid_prefix):
        raise HTTPException(400, "invalid prefix")
    rows = await conn.fetch(
        "SELECT sid FROM sessions WHERE user_id=$1 AND sid LIKE $2",
        user.user_id,
        sid_prefix + "%",
    )
    if len(rows) == 0:
        raise HTTPException(404, "session not found")
    if len(rows) > 1:
        raise HTTPException(409, "prefix matches multiple sessions")
    await conn.execute(
        "DELETE FROM sessions WHERE sid=$1 AND user_id=$2",
        rows[0]["sid"],
        user.user_id,
    )
    return Response(status_code=204)


class PresetIn(BaseModel):
    """Body for creating a saved filter preset."""

    agency_id: int
    name: str
    range_ctx: dict[str, Any]


class PresetOut(BaseModel):
    """Saved filter preset as returned to the caller."""

    preset_id: int
    agency_id: int
    name: str
    range_ctx: dict[str, Any]


@router.get("/me/presets", response_model=list[PresetOut])
async def list_presets(agency_id: int, user: User = Depends(require_user), conn=Depends(get_conn)):
    """List the caller's saved filter presets for ``agency_id``."""
    rows = await conn.fetch(
        "SELECT preset_id, agency_id, name, range_ctx::text AS range_ctx_text "
        "FROM filter_presets WHERE user_id=$1 AND agency_id=$2 ORDER BY created_at DESC",
        user.user_id,
        agency_id,
    )
    return [
        PresetOut(
            preset_id=r["preset_id"],
            agency_id=r["agency_id"],
            name=r["name"],
            range_ctx=json.loads(r["range_ctx_text"]),
        )
        for r in rows
    ]


@router.post("/me/presets", response_model=PresetOut, status_code=201)
async def create_preset(
    body: PresetIn,
    request: Request,
    user: User = Depends(require_user),
    conn=Depends(get_conn),
):
    """Save a filter preset; 409 if the name is already in use."""
    csrf_guard(request)
    try:
        row = await conn.fetchrow(
            """
            INSERT INTO filter_presets (user_id, agency_id, name, range_ctx)
            VALUES ($1, $2, $3, $4::jsonb)
            RETURNING preset_id, agency_id, name, range_ctx::text AS range_ctx_text
            """,
            user.user_id,
            body.agency_id,
            body.name,
            json.dumps(body.range_ctx),
        )
    except asyncpg.UniqueViolationError:
        raise HTTPException(409, "name already used") from None
    return PresetOut(
        preset_id=row["preset_id"],
        agency_id=row["agency_id"],
        name=row["name"],
        range_ctx=json.loads(row["range_ctx_text"]),
    )


@router.delete("/me/presets/{preset_id}", status_code=204)
async def delete_preset(
    preset_id: int,
    request: Request,
    user: User = Depends(require_user),
    conn=Depends(get_conn),
):
    """Delete one of the caller's filter presets."""
    csrf_guard(request)
    result = await conn.execute(
        "DELETE FROM filter_presets WHERE preset_id=$1 AND user_id=$2",
        preset_id,
        user.user_id,
    )
    if result.endswith(" 0"):
        raise HTTPException(404, "preset not found")
    return Response(status_code=204)
