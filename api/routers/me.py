"""Self-service endpoints for logged-in users."""

from typing import Any

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from api.deps import get_conn, get_pooled_conn
from api.middleware.session import SESSION_COOKIE_NAME
from api.security import User, csrf_guard, current_user, require_user, token_hash
from pipeline.admin_users import SessionPrefixError, resolve_session_prefix

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
    llm_approved: bool
    identities: list[IdentityOut]


@router.get("/me", response_model=MeOut | None)
async def get_me(
    user: User | None = Depends(current_user), conn: asyncpg.Connection = Depends(get_pooled_conn)
) -> MeOut | None:
    """Return the caller's profile and linked OAuth identities, or ``null`` when signed out.

    Signed out is an answer to who-am-I rather than a refusal, because the SPA asks it on
    every page, and it discloses nothing; every other ``/me`` route keeps ``require_user``.
    The connection is borrowed per statement, so a signed-out caller holds none.
    """
    if user is None:
        return None
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
        llm_approved=user.llm_approved,
        identities=[IdentityOut(**dict(r)) for r in rows],
    )


class SessionOut(BaseModel):
    """One active session row exposed to the caller.

    ``sid_prefix`` is a prefix of the stored ``sid_hash``, not of the session
    id itself — the server no longer holds the session id, and a handle the
    client can echo back must be derivable from what is stored. It stays an
    opaque display/lookup handle either way."""

    sid_prefix: str
    user_agent: str | None
    ip: str | None
    created_at: Any
    last_seen_at: Any
    # The session this request was made with, so the page can mark "this
    # device" and send its sign-out through the regular logout instead.
    current: bool


@router.get("/me/sessions", response_model=list[SessionOut])
async def list_sessions(
    request: Request, user: User = Depends(require_user), conn: asyncpg.Connection = Depends(get_conn)
) -> list[SessionOut]:
    """List the caller's active sessions, ordered by most-recent activity."""
    sid = request.cookies.get(SESSION_COOKIE_NAME)
    current_hash = token_hash(sid) if sid else None
    rows = await conn.fetch(
        "SELECT sid_hash, user_agent, ip::text AS ip, created_at, last_seen_at "
        "FROM sessions WHERE user_id=$1 AND expires_at > now() ORDER BY last_seen_at DESC",
        user.user_id,
    )
    return [
        SessionOut(
            sid_prefix=r["sid_hash"][:12],
            user_agent=r["user_agent"],
            ip=r["ip"],
            created_at=r["created_at"],
            last_seen_at=r["last_seen_at"],
            current=r["sid_hash"] == current_hash,
        )
        for r in rows
    ]


# Only narrows the fetch to the caller's sessions that start with the prefix.
# The boundary is resolve_session_prefix: it refuses anything but lowercase
# hex before this runs and re-checks every returned hash itself. The
# comparison is exact (left(), not LIKE, which reads `_` as a wildcard) so
# the two agree on what matches.
_SID_PREFIX_QUERY = "SELECT sid_hash FROM sessions WHERE user_id=$1 AND left(sid_hash, length($2)) = $2"


@router.delete("/me/sessions/{sid_prefix}", status_code=204)
async def revoke_session(
    sid_prefix: str,
    request: Request,
    user: User = Depends(require_user),
    conn: asyncpg.Connection = Depends(get_conn),
) -> Response:
    """Revoke the caller's session matching the given ``sid_hash`` prefix.

    Delegates to :func:`pipeline.admin_users.resolve_session_prefix` over the
    caller's own sessions only, answering with its
    :class:`~pipeline.admin_users.SessionPrefixError` when the prefix names
    no single session. Matching a prefix of the digest is safe in a way
    matching a prefix of the session id would not be: the digest is not a
    credential, so it can be echoed through a URL path.
    """
    csrf_guard(request)

    async def scoped_hashes() -> list[str]:
        return [r["sid_hash"] for r in await conn.fetch(_SID_PREFIX_QUERY, user.user_id, sid_prefix)]

    try:
        sid_hash = await resolve_session_prefix(sid_prefix, scoped_hashes)
    except SessionPrefixError as exc:
        raise HTTPException(exc.status, exc.detail) from None
    await conn.execute("DELETE FROM sessions WHERE sid_hash=$1 AND user_id=$2", sid_hash, user.user_id)
    return Response(status_code=204)
