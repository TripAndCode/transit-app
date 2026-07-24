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

import logging
import os
import secrets
from datetime import datetime, timedelta, timezone

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from itsdangerous import BadSignature, URLSafeTimedSerializer
from pydantic import BaseModel

from api.deps import get_conn
from api.middleware.ratelimit import limiter
from api.oauth import oauth
from api.security import User, cookie_secure, csrf_guard, current_user, hash_password, verify_password
from pipeline.audit import record_event

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])

SESSION_COOKIE_NAME = os.environ.get("SESSION_COOKIE_NAME", "sid")
SESSION_TTL_DAYS = int(os.environ.get("SESSION_TTL_DAYS", "30"))
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://localhost:8000")
ADMIN_EMAILS = {e.strip().lower() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()}
TX_COOKIE = "oauth_tx"
TX_TTL_SEC = 5 * 60


def local_admin_enabled() -> bool:
    """True when both break-glass local-admin env vars are set. Read live
    (not import-frozen) so tests can monkeypatch + reimport like the OAuth vars."""
    return bool(os.environ.get("DEFAULT_ADMIN_USERNAME")) and bool(os.environ.get("DEFAULT_ADMIN_PASSWORD"))


async def seed_local_admin(pool: asyncpg.Pool) -> None:
    """Upsert the break-glass local-admin account from DEFAULT_ADMIN_USERNAME/
    DEFAULT_ADMIN_PASSWORD at startup (api.main's lifespan). No-ops if either
    is unset. Re-hashes and re-applies the password on every boot, so rotating
    it is just editing .env and restarting — the same mental model as
    rotating an OAuth client secret. Role is force-set to 'admin' every time:
    this account only exists to bootstrap/break-glass, it should never end up
    demoted by accident.

    Refuses to seed over an email that already belongs to a real
    OAuth-provisioned account (has a linked ``oauth_identities`` row) —
    without this, an operator picking ``DEFAULT_ADMIN_USERNAME`` that
    happens to match a live SSO user's email would silently grant that
    user's account a password login and force-promote it to admin on
    every restart. Promoting a pre-existing (non-OAuth) account is still
    allowed but leaves a ``role_changed`` audit event, matching how every
    other role change in the admin surface is audited.
    """
    if not local_admin_enabled():
        return
    username = os.environ["DEFAULT_ADMIN_USERNAME"]
    password_hash = hash_password(os.environ["DEFAULT_ADMIN_PASSWORD"])
    async with pool.acquire() as conn:
        existing = await conn.fetchrow("SELECT user_id, role FROM users WHERE email=$1", username)
        if existing is not None:
            has_oauth = await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM oauth_identities WHERE user_id=$1)", existing["user_id"]
            )
            if has_oauth:
                _log.error(
                    "DEFAULT_ADMIN_USERNAME=%r matches an existing OAuth-linked account "
                    "(user_id=%s) — refusing to seed the break-glass local-admin over a "
                    "real SSO user. Choose a DEFAULT_ADMIN_USERNAME that isn't a live user's email.",
                    username,
                    existing["user_id"],
                )
                return
        row = await conn.fetchrow(
            """
            INSERT INTO users (email, name, role, password_hash)
            VALUES ($1, 'Local Admin', 'admin', $2)
            ON CONFLICT (email) DO UPDATE SET password_hash = EXCLUDED.password_hash, role = 'admin'
            RETURNING user_id
            """,
            username,
            password_hash,
        )
        if existing is not None and existing["role"] != "admin":
            await record_event(
                conn,
                user_id=row["user_id"],
                actor_id=None,
                kind="role_changed",
                meta={"old": existing["role"], "new": "admin", "reason": "break_glass_seed"},
            )


_signer = URLSafeTimedSerializer(
    os.environ.get("SESSION_SIGNING_KEY", "dev-only-not-secret"),
    salt="oauth-tx",
)


def _require_sso_configured() -> None:
    """503 the request when any OAuth env var is unset. ``GET /api/config``
    advertises this state so the SPA hides login UI; this guard catches
    direct hits to the OAuth endpoints (curl, stale browser tabs)."""
    required = (
        "SESSION_SIGNING_KEY",
        "GOOGLE_CLIENT_ID",
        "GOOGLE_CLIENT_SECRET",
        "GITHUB_CLIENT_ID",
        "GITHUB_CLIENT_SECRET",
    )
    if any(not os.environ.get(k) for k in required):
        raise HTTPException(status_code=503, detail="sso not configured")


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


@router.get("/{provider}/login")
async def login(provider: str, request: Request, next: str = "/"):
    """Redirect the browser to ``provider`` OAuth. Stashes state + PKCE verifier
    + sanitized next URL in a signed short-lived cookie that the callback verifies.
    """
    _require_sso_configured()
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
        secure=cookie_secure(),
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


async def _upsert_user(
    conn,
    provider: str,
    info: dict,
    *,
    ip: str | None = None,
    user_agent: str | None = None,
) -> tuple[int, str]:
    """Returns (user_id, role). Auto-links by verified email; promotes per ADMIN_EMAILS.
    ``ip`` / ``user_agent`` are forwarded onto the account_created audit row when
    a fresh user is created."""
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
        # the same email. ``xmax = 0`` distinguishes a fresh INSERT (new
        # account) from the UPDATE path so we can emit account_created
        # exactly once per user.
        row = await conn.fetchrow(
            """
            INSERT INTO users (email, name, avatar_url) VALUES ($1, $2, $3)
            ON CONFLICT (email) DO UPDATE SET email = EXCLUDED.email
            RETURNING user_id, (xmax = 0) AS is_new
            """,
            email,
            info.get("name"),
            info.get("avatar_url"),
        )
        uid = row["user_id"]
        if row["is_new"]:
            await record_event(
                conn,
                user_id=uid,
                actor_id=uid,
                kind="account_created",
                provider=provider,
                ip=ip,
                user_agent=user_agent,
            )
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


async def _create_session(conn, uid: int, ua: str | None, ip: str | None) -> str:
    """Insert a sessions row for ``uid`` and return the new ``sid``. Shared by
    the OAuth callback and the local-admin login — both mint a session the
    same way once they've settled on a user_id."""
    sid = secrets.token_urlsafe(32)
    await conn.execute(
        "INSERT INTO sessions (sid, user_id, expires_at, user_agent, ip) VALUES ($1, $2, $3, $4, $5::inet)",
        sid,
        uid,
        datetime.now(timezone.utc) + timedelta(days=SESSION_TTL_DAYS),
        ua,
        ip,
    )
    return sid


def _set_session_cookie(resp, sid: str) -> None:
    resp.set_cookie(
        SESSION_COOKIE_NAME,
        sid,
        max_age=SESSION_TTL_DAYS * 86400,
        httponly=True,
        secure=cookie_secure(),
        samesite="lax",
        path="/",
    )


async def _fail_login(conn, request: Request, provider: str, reason: str) -> RedirectResponse:
    """Audit + redirect helper for OAuth callback failure paths."""
    await record_event(
        conn,
        user_id=None,
        actor_id=None,
        kind="login_failed",
        provider=provider,
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        meta={"reason": reason},
    )
    return RedirectResponse(url=f"/login?error={reason}", status_code=302)


@router.get("/{provider}/callback")
async def callback(provider: str, request: Request, conn: asyncpg.Connection = Depends(get_conn)):
    """OAuth provider redirects here with ``code`` and ``state``. Validate against
    the signed ``oauth_tx`` cookie, exchange the code, upsert user + session,
    set the ``sid`` cookie, and redirect to the sanitized next URL.
    """
    _require_sso_configured()
    if provider not in ("google", "github"):
        raise HTTPException(404, "unknown provider")
    tx_raw = request.cookies.get(TX_COOKIE)
    if not tx_raw:
        return await _fail_login(conn, request, provider, "state")
    try:
        tx = _signer.loads(tx_raw, max_age=TX_TTL_SEC)
    except BadSignature:
        return await _fail_login(conn, request, provider, "state")
    if tx.get("provider") != provider:
        return await _fail_login(conn, request, provider, "state")

    state_query = request.query_params.get("state")
    if state_query != tx.get("state"):
        return await _fail_login(conn, request, provider, "state")

    client = oauth.create_client(provider)
    try:
        # Do NOT pass code_verifier explicitly here — authorize_redirect (in
        # the /login handler) already stashed it in the Starlette session via
        # Authlib's own state machinery, and authorize_access_token's
        # _format_state_params() pulls it back out of that session state
        # automatically. Supplying it again as a kwarg collided with that
        # (TypeError: got multiple values for keyword argument
        # 'code_verifier'), breaking every real login attempt in production —
        # the tx cookie is the actual security boundary (state/TTL/signature
        # check above), not this now-redundant kwarg.
        token = await client.authorize_access_token(request)
    except Exception:
        _log.exception("OAuth token exchange failed for provider=%s", provider)
        return await _fail_login(conn, request, provider, "provider_down")

    info = await _fetch_userinfo(client, token, provider)
    if not info["email"] or not info["email_verified"]:
        code = "unverified_email" if info["email"] else "no_email"
        return await _fail_login(conn, request, provider, code)

    ua = request.headers.get("user-agent")
    ip = request.client.host if request.client else None
    async with conn.transaction():
        uid, _role = await _upsert_user(conn, provider, info, ip=ip, user_agent=ua)
        sid = await _create_session(conn, uid, ua, ip)
        await record_event(conn, user_id=uid, actor_id=uid, kind="login", provider=provider, ip=ip, user_agent=ua)

    next_url = sanitize_next(tx.get("next"))
    resp = RedirectResponse(url=next_url, status_code=302)
    _set_session_cookie(resp, sid)
    resp.delete_cookie(TX_COOKIE, path="/")
    return resp


class LocalLoginBody(BaseModel):
    username: str
    password: str


@router.post("/local/login")
@limiter.limit("5/minute")
async def local_login(
    request: Request,
    body: LocalLoginBody,
    conn: asyncpg.Connection = Depends(get_conn),
):
    """Password login for the single break-glass admin account (seeded at
    startup from DEFAULT_ADMIN_USERNAME/DEFAULT_ADMIN_PASSWORD — see
    api.main's lifespan). Exists so there's always a way into /admin that
    doesn't depend on OAuth being configured or reachable. Rate-limited
    per IP; every attempt (success or failure) is audited to login_events
    the same way OAuth failures already are.
    """
    if not local_admin_enabled():
        raise HTTPException(status_code=503, detail="local admin login not configured")

    ua = request.headers.get("user-agent")
    ip = request.client.host if request.client else None
    row = await conn.fetchrow(
        "SELECT user_id, password_hash FROM users WHERE email=$1",
        body.username,
    )
    if row is None or not verify_password(body.password, row["password_hash"]):
        await record_event(
            conn,
            user_id=None,
            actor_id=None,
            kind="login_failed",
            provider="local",
            ip=ip,
            user_agent=ua,
            meta={"reason": "bad_credentials", "username": body.username},
        )
        return JSONResponse(status_code=401, content={"error": "invalid_credentials"})

    uid = row["user_id"]
    async with conn.transaction():
        sid = await _create_session(conn, uid, ua, ip)
        await record_event(conn, user_id=uid, actor_id=uid, kind="login", provider="local", ip=ip, user_agent=ua)

    resp = JSONResponse(status_code=200, content={"ok": True})
    _set_session_cookie(resp, sid)
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
