"""OAuth login, callback, logout.

The login handler kicks off the Authlib redirect to the provider; the
callback verifies a signed ``oauth_tx`` cookie (state + PKCE verifier +
post-login next URL + provider) we set on the way out, exchanges the
authorization code, normalizes user info, upserts the user + identity,
mints a session row, and sets the long-lived ``sid`` cookie. Logout
deletes the session row and clears the cookie.

State for the OAuth handshake lives in a short-lived signed cookie
(``oauth_tx``) rather than the Starlette session, so we don't depend on
sticky server-side state between login start and callback. Starlette's
``SessionMiddleware`` is still mounted because Authlib's helpers read
``request.session`` defensively; we just don't rely on it for security.
"""

import os
import secrets
from datetime import datetime, timedelta, timezone

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature, URLSafeTimedSerializer

from api.deps import get_conn
from api.oauth import oauth
from api.security import User, csrf_guard, current_user
from pipeline.audit import record_event

router = APIRouter(prefix="/api/auth", tags=["auth"])

SESSION_COOKIE_NAME = os.environ.get("SESSION_COOKIE_NAME", "sid")
SESSION_TTL_DAYS = int(os.environ.get("SESSION_TTL_DAYS", "30"))
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://localhost:8000")
ADMIN_EMAILS = {e.strip().lower() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()}
TX_COOKIE = "oauth_tx"
TX_TTL_SEC = 5 * 60

_signer = URLSafeTimedSerializer(
    os.environ.get("SESSION_SIGNING_KEY", "dev-only-not-secret"),
    salt="oauth-tx",
)


def sanitize_next(value: str | None) -> str:
    """Return a safe relative path for post-login redirect, defending against
    open-redirect attacks. Only same-origin absolute paths are kept.
    """
    if not value:
        return "/"
    if value.startswith("//") or "://" in value:
        return "/"
    if not value.startswith("/"):
        return "/"
    return value


def _is_secure() -> bool:
    return PUBLIC_BASE_URL.startswith("https://")


@router.get("/{provider}/login")
async def login(provider: str, request: Request, next: str = "/"):
    """Redirect the browser to ``provider`` OAuth. Stashes state + PKCE verifier
    + sanitized next URL in a signed short-lived cookie that the callback verifies.
    """
    if provider not in ("google", "github"):
        raise HTTPException(404, "unknown provider")
    safe_next = sanitize_next(next)
    redirect_uri = f"{PUBLIC_BASE_URL}/api/auth/{provider}/callback"
    client = oauth.create_client(provider)
    state = secrets.token_urlsafe(24)
    code_verifier = secrets.token_urlsafe(48)
    tx_payload = _signer.dumps({"state": state, "verifier": code_verifier, "next": safe_next, "provider": provider})
    auth_url_resp = await client.authorize_redirect(
        request,
        redirect_uri,
        state=state,
        code_verifier=code_verifier,
    )
    auth_url_resp.set_cookie(
        TX_COOKIE,
        tx_payload,
        max_age=TX_TTL_SEC,
        httponly=True,
        secure=_is_secure(),
        samesite="lax",
        path="/",
    )
    return auth_url_resp


async def _fetch_userinfo(client, token, provider: str) -> dict:
    """Normalize provider userinfo to {sub, email, email_verified, name, avatar_url}."""
    if provider == "google":
        info = token.get("userinfo")
        if not info:
            resp = await client.get("https://openidconnect.googleapis.com/v1/userinfo", token=token)
            info = resp.json()
        return {
            "sub": info["sub"],
            "email": info.get("email"),
            "email_verified": info.get("email_verified") is True,
            "name": info.get("name"),
            "avatar_url": info.get("picture"),
        }
    # github
    user_resp = await client.get("user", token=token)
    user = user_resp.json()
    emails_resp = await client.get("user/emails", token=token)
    emails = emails_resp.json()
    primary = next(
        (e for e in emails if e.get("primary") and e.get("verified")),
        None,
    )
    return {
        "sub": str(user["id"]),
        "email": primary["email"] if primary else None,
        "email_verified": primary is not None,
        "name": user.get("name") or user.get("login"),
        "avatar_url": user.get("avatar_url"),
    }


async def _upsert_user(conn, provider: str, info: dict) -> tuple[int, str]:
    """Returns (user_id, role). Auto-links by verified email; promotes per ADMIN_EMAILS."""
    sub = info["sub"]
    email = info["email"]

    # 1. Match by (provider, sub)
    row = await conn.fetchrow(
        "SELECT user_id FROM oauth_identities WHERE provider=$1 AND provider_sub=$2",
        provider,
        sub,
    )
    if row:
        uid = row["user_id"]
    else:
        # 2. Match by verified email (auto-link). ON CONFLICT DO UPDATE is a
        # no-op write that lets us use RETURNING whether the row was new or
        # existing — race-tolerant against concurrent first-time logins for
        # the same email.
        row = await conn.fetchrow(
            """
            INSERT INTO users (email, name, avatar_url) VALUES ($1, $2, $3)
            ON CONFLICT (email) DO UPDATE SET email = EXCLUDED.email
            RETURNING user_id
            """,
            email,
            info.get("name"),
            info.get("avatar_url"),
        )
        uid = row["user_id"]
        await conn.execute(
            "INSERT INTO oauth_identities (provider, provider_sub, user_id, email_at_link) "
            "VALUES ($1, $2, $3, $4) ON CONFLICT DO NOTHING",
            provider,
            sub,
            uid,
            email,
        )

    # ADMIN_EMAILS promotion (one-way). Atomic UPDATE-then-RETURNING avoids
    # duplicate audit rows when concurrent callbacks for the same email race.
    user_row = await conn.fetchrow(
        "SELECT email, role FROM users WHERE user_id=$1",
        uid,
    )
    role = user_row["role"]
    if role != "admin" and (user_row["email"] or "").lower() in ADMIN_EMAILS:
        promoted = await conn.fetchval(
            "UPDATE users SET role='admin' WHERE user_id=$1 AND role <> 'admin' RETURNING role",
            uid,
        )
        if promoted == "admin":
            await record_event(
                conn,
                user_id=uid,
                actor_id=uid,
                kind="role_changed",
                meta={"old": role, "new": "admin", "via": "ADMIN_EMAILS"},
            )
            role = "admin"
    return uid, role


@router.get("/{provider}/callback")
async def callback(provider: str, request: Request, conn: asyncpg.Connection = Depends(get_conn)):
    """OAuth provider redirects here with ``code`` and ``state``. Validate against
    the signed ``oauth_tx`` cookie, exchange the code, upsert user + session,
    set the ``sid`` cookie, and redirect to the sanitized next URL.
    """
    if provider not in ("google", "github"):
        raise HTTPException(404, "unknown provider")
    tx_raw = request.cookies.get(TX_COOKIE)
    if not tx_raw:
        return RedirectResponse(url="/login?error=state", status_code=302)
    try:
        tx = _signer.loads(tx_raw, max_age=TX_TTL_SEC)
    except BadSignature:
        return RedirectResponse(url="/login?error=state", status_code=302)
    if tx.get("provider") != provider:
        return RedirectResponse(url="/login?error=state", status_code=302)

    state_query = request.query_params.get("state")
    if state_query != tx.get("state"):
        return RedirectResponse(url="/login?error=state", status_code=302)

    client = oauth.create_client(provider)
    try:
        # Authlib reads the state from request.session by default; we pass
        # code_verifier explicitly since we kept it in our signed cookie.
        token = await client.authorize_access_token(
            request,
            code_verifier=tx["verifier"],
        )
    except Exception:
        return RedirectResponse(url="/login?error=provider_down", status_code=302)

    info = await _fetch_userinfo(client, token, provider)
    if not info["email"] or not info["email_verified"]:
        code = "unverified_email" if info["email"] else "no_email"
        return RedirectResponse(url=f"/login?error={code}", status_code=302)

    async with conn.transaction():
        uid, role = await _upsert_user(conn, provider, info)
        sid = secrets.token_urlsafe(32)
        ua = request.headers.get("user-agent")
        ip = request.client.host if request.client else None
        await conn.execute(
            "INSERT INTO sessions (sid, user_id, expires_at, user_agent, ip) VALUES ($1, $2, $3, $4, $5::inet)",
            sid,
            uid,
            datetime.now(timezone.utc) + timedelta(days=SESSION_TTL_DAYS),
            ua,
            ip,
        )
        await record_event(conn, user_id=uid, actor_id=uid, kind="login", provider=provider, ip=ip, user_agent=ua)

    next_url = sanitize_next(tx.get("next"))
    resp = RedirectResponse(url=next_url, status_code=302)
    resp.set_cookie(
        SESSION_COOKIE_NAME,
        sid,
        max_age=SESSION_TTL_DAYS * 86400,
        httponly=True,
        secure=_is_secure(),
        samesite="lax",
        path="/",
    )
    resp.delete_cookie(TX_COOKIE, path="/")
    return resp


@router.post("/logout", status_code=204)
async def logout(
    request: Request,
    user: User | None = Depends(current_user),
    conn: asyncpg.Connection = Depends(get_conn),
):
    """Delete the session row and clear the ``sid`` cookie. CSRF-guarded.

    Idempotent: succeeds with 204 even if no session is present, so a
    user whose cookie already expired/cleared doesn't see "logout failed".
    """
    csrf_guard(request)
    sid = request.cookies.get(SESSION_COOKIE_NAME)
    if sid and user:
        await conn.execute("DELETE FROM sessions WHERE sid=$1 AND user_id=$2", sid, user.user_id)
        ua = request.headers.get("user-agent")
        ip = request.client.host if request.client else None
        await record_event(conn, user_id=user.user_id, actor_id=user.user_id, kind="logout", ip=ip, user_agent=ua)
    resp = Response(status_code=204)
    resp.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return resp
