"""Self-service endpoints for logged-in users."""

from typing import Any

import asyncpg
import openai
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from api.deps import get_conn, get_pooled_conn
from api.middleware.ratelimit import limiter, tier_limit
from api.middleware.session import SESSION_COOKIE_NAME
from api.range import jst_today
from api.security import User, csrf_guard, current_user, require_user, token_hash
from pipeline.account_erasure import ErasureRefused, erase_user
from pipeline.account_export import collect_user_data
from pipeline.admin_users import SessionPrefixError, resolve_session_prefix
from pipeline.query.llm_key_validation import validate_provider_key
from pipeline.query.user_llm_keys import (
    ALLOWED_PROVIDERS,
    delete_user_llm_key,
    get_user_llm_key,
    key_suffix,
    save_user_llm_key,
)

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
    every page. It discloses nothing, so the login gate lets a signed-out GET through even
    while sign-in is required; every other ``/me`` route keeps ``require_user`` and the
    gate. The connection is borrowed per statement, so a signed-out caller holds none.
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


class LLMKeyStatus(BaseModel):
    """Status of the caller's stored BYOK LLM key — never the raw key itself."""

    configured: bool
    provider: str | None = None
    key_suffix: str | None = None


class LLMKeyPut(BaseModel):
    """Body for storing (or replacing) the caller's BYOK LLM key."""

    provider: str
    api_key: str


@router.get("/me/llm-key", response_model=LLMKeyStatus)
async def get_llm_key(user: User = Depends(require_user), conn: asyncpg.Connection = Depends(get_conn)) -> LLMKeyStatus:
    """Return whether the caller has a BYOK LLM key configured, and its masked suffix."""
    key = await get_user_llm_key(conn, user.user_id)
    if key is None:
        return LLMKeyStatus(configured=False)
    return LLMKeyStatus(configured=True, provider=key.provider, key_suffix=key.key_suffix)


@router.put("/me/llm-key", response_model=LLMKeyStatus)
@limiter.limit(tier_limit)
async def put_llm_key(
    body: LLMKeyPut,
    request: Request,
    user: User = Depends(require_user),
) -> LLMKeyStatus:
    """Validate then store the caller's BYOK LLM key.

    Validation runs before ``save_user_llm_key`` is ever called, so a bad key
    is rejected with 400 and never persisted. Unlike the other routes in this
    file, this does not take ``conn: Depends(get_conn)`` — the pooled
    connection is acquired only after ``validate_provider_key``'s external
    HTTPS round-trip (up to a 10s timeout) succeeds, so it never sits idle in
    the pool for that call's duration.
    """
    csrf_guard(request)
    if body.provider not in ALLOWED_PROVIDERS:
        raise HTTPException(status_code=400, detail=f"unsupported provider: {body.provider}")
    try:
        valid = await validate_provider_key(body.provider, body.api_key)
    except (openai.APIConnectionError, openai.APITimeoutError):
        # A network/timeout failure proves nothing about the key itself —
        # tell the caller we couldn't check right now (503), distinct from
        # the key actually being rejected (400).
        raise HTTPException(status_code=503, detail="validation_unavailable") from None
    if not valid:
        raise HTTPException(status_code=400, detail="key_rejected")
    async with request.app.state.pool.acquire() as conn:
        await save_user_llm_key(conn, user.user_id, body.provider, body.api_key)
    return LLMKeyStatus(configured=True, provider=body.provider, key_suffix=key_suffix(body.api_key))


@router.delete("/me/llm-key", status_code=204)
async def delete_llm_key(
    request: Request,
    user: User = Depends(require_user),
    conn: asyncpg.Connection = Depends(get_conn),
) -> Response:
    """Delete the caller's stored BYOK LLM key, if any."""
    csrf_guard(request)
    await delete_user_llm_key(conn, user.user_id)
    return Response(status_code=204)


class DeleteMeBody(BaseModel):
    """The caller types their own email to confirm deleting the account."""

    confirm_email: str


@router.delete("/me", status_code=204)
@limiter.limit("5/minute")
async def delete_me(
    body: DeleteMeBody,
    request: Request,
    user: User = Depends(require_user),
    conn: asyncpg.Connection = Depends(get_conn),
) -> Response:
    """Hard-delete the caller's account and every personal row tied to it."""
    csrf_guard(request)
    if body.confirm_email.strip().lower() != user.email.lower():
        raise HTTPException(status_code=400, detail="confirmation_mismatch")
    try:
        async with conn.transaction():
            await erase_user(conn, user.user_id)
    except ErasureRefused as exc:
        raise HTTPException(status_code=409, detail=exc.reason) from None
    resp = Response(status_code=204)
    resp.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return resp


class AccountExport(BaseModel):
    """The download's JSON shape: the caller's personal data, as
    ``pipeline.account_export.collect_user_data`` defines it."""

    exported_at: Any
    profile: dict[str, Any] | None
    identities: list[dict[str, Any]]
    sessions: list[dict[str, Any]]
    login_events: list[dict[str, Any]]
    presets: list[dict[str, Any]]
    conversations: list[dict[str, Any]]
    llm_key: dict[str, Any] | None
    api_keys: list[dict[str, Any]]
    usage_daily: list[dict[str, Any]]
    invites: list[dict[str, Any]]
    admin_actions: list[dict[str, Any]]
    admin_account_events: list[dict[str, Any]]


@router.get("/me/export", response_model=AccountExport)
@limiter.limit("5/minute")
async def export_me(
    request: Request,
    user: User = Depends(require_user),
    conn: asyncpg.Connection = Depends(get_conn),
) -> JSONResponse:
    """The caller's personal data, as a JSON download."""
    data = await collect_user_data(conn, user.user_id)
    filename = f"transit-app-export-{jst_today().isoformat()}.json"
    return JSONResponse(jsonable_encoder(data), headers={"Content-Disposition": f'attachment; filename="{filename}"'})
