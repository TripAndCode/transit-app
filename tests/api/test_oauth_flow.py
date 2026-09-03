"""End-to-end OAuth callback + logout coverage with Authlib's create_client
patched out. We never hit a real provider; the userinfo path is monkeypatched
to return canned dicts.

Each test builds a valid signed ``oauth_tx`` cookie via ``auth_mod._signer.dumps``
so the callback's state check accepts it, then asserts redirect target +
database side effects (users, oauth_identities, sessions, login_events).
"""

import os
from datetime import datetime
from unittest.mock import AsyncMock, patch

import asyncpg
import httpx
import pytest
from httpx import ASGITransport

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://localhost/transit")


@pytest.fixture(autouse=True)
def _set_oauth_env(monkeypatch):
    """Stuff env vars that auth.py reads live (os.environ.get at call time —
    _require_sso_configured, PUBLIC_BASE_URL, etc.), so plain monkeypatch.setenv
    is enough with no reload.

    ADMIN_EMAILS is the one exception: it's built at import-time as a frozen
    set, so monkeypatch.setenv("ADMIN_EMAILS", ...) alone wouldn't change it.
    Tests that need a different set monkeypatch ``auth_mod.ADMIN_EMAILS``
    directly instead (see test_admin_email_promotes) — cheaper and more
    explicit than reloading the whole module to re-freeze it.

    Historical note: this fixture used to importlib.reload(api.routers.auth)
    on every test. That's unnecessary (everything it "refreshed" either reads
    env live already, per above, or — like the oauth-tx signer — is fine
    staying frozen at whatever it was on first import, since every test
    round-trips through the same frozen instance). Worse, the reload re-ran
    auth.py's `@limiter.limit(...)` decorators each time, registering a
    duplicate rate-limit rule against the shared slowapi Limiter singleton
    per test — 12 tests here meant 12 accumulated duplicate rules for any
    rate-limited auth route, which was silently starving that route's quota
    in a DIFFERENT test file's tests whenever both ran in the same session.
    """
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "g")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "gs")
    monkeypatch.setenv("GITHUB_CLIENT_ID", "h")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "hs")
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://test")
    monkeypatch.setenv("ADMIN_EMAILS", "")
    monkeypatch.setenv("SESSION_SIGNING_KEY", "test-signing-key")
    yield


@pytest.fixture
async def auth_client(apply_schema):
    from api.main import app

    pool = await asyncpg.create_pool(DATABASE_URL)
    app.state.pool = pool
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    await pool.close()


@pytest.mark.asyncio
async def test_callback_with_bad_state_redirects_to_error(auth_client):
    resp = await auth_client.get(
        "/api/auth/google/callback?code=abc&state=mismatch",
        cookies={"oauth_tx": "garbage"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "/login?error=state" in resp.headers["location"]


@pytest.mark.asyncio
async def test_callback_unverified_email_redirects(auth_client, aconn, monkeypatch):
    """Mock the userinfo path to return verified=False."""
    from api.routers import auth as auth_mod

    async def fake_userinfo(client, token, provider):
        return {"sub": "x", "email": "a@x", "email_verified": False, "name": "A", "avatar_url": None}

    monkeypatch.setattr(auth_mod, "_fetch_userinfo", fake_userinfo)

    # build a valid tx cookie ourselves
    payload = auth_mod._signer.dumps({"state": "s", "verifier": "v", "next": "/", "provider": "google"})

    # mock authorize_access_token to skip real provider call
    fake_token = {"access_token": "tok"}
    client_mock = AsyncMock()
    client_mock.authorize_access_token = AsyncMock(return_value=fake_token)
    with patch.object(auth_mod.oauth, "create_client", return_value=client_mock):
        resp = await auth_client.get(
            "/api/auth/google/callback?state=s&code=c",
            cookies={"oauth_tx": payload},
            follow_redirects=False,
        )
    assert resp.status_code == 302
    assert "error=unverified_email" in resp.headers["location"]


@pytest.mark.asyncio
async def test_callback_creates_user_and_session(auth_client, aconn, monkeypatch):
    from api.routers import auth as auth_mod

    async def fake_userinfo(client, token, provider):
        return {
            "sub": "google-sub-1",
            "email": "yo@x",
            "email_verified": True,
            "name": "Yo",
            "avatar_url": "http://a/x.png",
        }

    monkeypatch.setattr(auth_mod, "_fetch_userinfo", fake_userinfo)
    payload = auth_mod._signer.dumps({"state": "s", "verifier": "v", "next": "/", "provider": "google"})
    fake_token = {"access_token": "tok"}
    client_mock = AsyncMock()
    client_mock.authorize_access_token = AsyncMock(return_value=fake_token)
    with patch.object(auth_mod.oauth, "create_client", return_value=client_mock):
        resp = await auth_client.get(
            "/api/auth/google/callback?state=s&code=c",
            cookies={"oauth_tx": payload},
            follow_redirects=False,
        )
    assert resp.status_code == 302
    assert resp.headers["location"] == "/"
    assert "sid=" in resp.headers.get("set-cookie", "")

    row = await aconn.fetchrow("SELECT user_id, email, role FROM users WHERE email='yo@x'")
    assert row is not None
    assert row["role"] == "user"
    s = await aconn.fetchrow("SELECT sid FROM sessions WHERE user_id=$1", row["user_id"])
    assert s is not None


@pytest.mark.asyncio
async def test_admin_email_promotes(auth_client, aconn, monkeypatch):
    from api.routers import auth as auth_mod

    monkeypatch.setattr(auth_mod, "ADMIN_EMAILS", {"boss@x"})

    async def fake_userinfo(client, token, provider):
        return {"sub": "g2", "email": "boss@x", "email_verified": True, "name": "Boss", "avatar_url": None}

    monkeypatch.setattr(auth_mod, "_fetch_userinfo", fake_userinfo)
    payload = auth_mod._signer.dumps({"state": "s", "verifier": "v", "next": "/", "provider": "google"})
    client_mock = AsyncMock()
    client_mock.authorize_access_token = AsyncMock(return_value={"access_token": "t"})
    with patch.object(auth_mod.oauth, "create_client", return_value=client_mock):
        await auth_client.get(
            "/api/auth/google/callback?state=s&code=c",
            cookies={"oauth_tx": payload},
            follow_redirects=False,
        )
    role = await aconn.fetchval("SELECT role FROM users WHERE email='boss@x'")
    assert role == "admin"


@pytest.mark.asyncio
async def test_account_linking_by_verified_email(auth_client, aconn, monkeypatch):
    """User exists from Google login; same verified email via GitHub auto-links."""
    uid = (await aconn.fetchrow("INSERT INTO users (email, name) VALUES ('shared@x', 'A') RETURNING user_id"))[
        "user_id"
    ]
    await aconn.execute(
        "INSERT INTO oauth_identities (provider, provider_sub, user_id, email_at_link) "
        "VALUES ('google', 'g-sub', $1, 'shared@x')",
        uid,
    )

    from api.routers import auth as auth_mod

    async def fake_userinfo(client, token, provider):
        return {"sub": "gh-sub", "email": "shared@x", "email_verified": True, "name": "A", "avatar_url": None}

    monkeypatch.setattr(auth_mod, "_fetch_userinfo", fake_userinfo)
    payload = auth_mod._signer.dumps({"state": "s", "verifier": "v", "next": "/", "provider": "github"})
    client_mock = AsyncMock()
    client_mock.authorize_access_token = AsyncMock(return_value={"access_token": "t"})
    with patch.object(auth_mod.oauth, "create_client", return_value=client_mock):
        await auth_client.get(
            "/api/auth/github/callback?state=s&code=c",
            cookies={"oauth_tx": payload},
            follow_redirects=False,
        )
    rows = await aconn.fetch(
        "SELECT provider FROM oauth_identities WHERE user_id=$1 ORDER BY provider",
        uid,
    )
    assert [r["provider"] for r in rows] == ["github", "google"]
    n_users = await aconn.fetchval("SELECT count(*) FROM users WHERE email='shared@x'")
    assert n_users == 1


@pytest.mark.asyncio
async def test_oauth_login_refuses_to_take_over_a_local_password_account(auth_client, aconn, monkeypatch):
    """A break-glass/local-password account (password_hash set, no linked
    OAuth identity) must not be silently claimed by an OAuth login whose
    verified email happens to match — that would hand whoever controls the
    email address a live admin session, the same takeover class
    seed_local_admin's OAuth-linked guard closes in the other direction."""
    uid = (
        await aconn.fetchrow(
            "INSERT INTO users (email, name, role, password_hash) "
            "VALUES ('root@local', 'Local Admin', 'admin', 'hash') RETURNING user_id"
        )
    )["user_id"]

    from api.routers import auth as auth_mod

    async def fake_userinfo(client, token, provider):
        return {"sub": "g-sub", "email": "root@local", "email_verified": True, "name": "Someone", "avatar_url": None}

    monkeypatch.setattr(auth_mod, "_fetch_userinfo", fake_userinfo)
    payload = auth_mod._signer.dumps({"state": "s", "verifier": "v", "next": "/", "provider": "google"})
    client_mock = AsyncMock()
    client_mock.authorize_access_token = AsyncMock(return_value={"access_token": "t"})
    with patch.object(auth_mod.oauth, "create_client", return_value=client_mock):
        resp = await auth_client.get(
            "/api/auth/google/callback?state=s&code=c",
            cookies={"oauth_tx": payload},
            follow_redirects=False,
        )
    assert resp.status_code == 302
    assert "error=" in resp.headers["location"]
    # No OAuth identity got linked to the local account, and no session was minted for it.
    n_identities = await aconn.fetchval("SELECT count(*) FROM oauth_identities WHERE user_id=$1", uid)
    assert n_identities == 0
    n_sessions = await aconn.fetchval("SELECT count(*) FROM sessions WHERE user_id=$1", uid)
    assert n_sessions == 0


@pytest.mark.asyncio
async def test_first_login_emits_account_created(auth_client, aconn, monkeypatch):
    """Brand-new user: account_created fires once, alongside the login event."""
    from api.routers import auth as auth_mod

    async def fake_userinfo(client, token, provider):
        return {"sub": "new-sub", "email": "new@x", "email_verified": True, "name": "N", "avatar_url": None}

    monkeypatch.setattr(auth_mod, "_fetch_userinfo", fake_userinfo)
    payload = auth_mod._signer.dumps({"state": "s", "verifier": "v", "next": "/", "provider": "google"})
    client_mock = AsyncMock()
    client_mock.authorize_access_token = AsyncMock(return_value={"access_token": "t"})
    with patch.object(auth_mod.oauth, "create_client", return_value=client_mock):
        await auth_client.get(
            "/api/auth/google/callback?state=s&code=c",
            cookies={"oauth_tx": payload},
            follow_redirects=False,
        )
    uid = await aconn.fetchval("SELECT user_id FROM users WHERE email='new@x'")
    kinds = [
        r["kind"] for r in await aconn.fetch("SELECT kind FROM login_events WHERE user_id=$1 ORDER BY event_id", uid)
    ]
    assert kinds == ["account_created", "login"]


@pytest.mark.asyncio
async def test_login_event_and_session_fields_on_successful_callback(auth_client, aconn, monkeypatch):
    """Field-level characterization of the shared `_mint_session_and_log_login`
    sequence, exercised here via the OAuth callback()."""
    from api.routers import auth as auth_mod

    async def fake_userinfo(client, token, provider):
        return {"sub": "field-sub", "email": "field@x", "email_verified": True, "name": "F", "avatar_url": None}

    monkeypatch.setattr(auth_mod, "_fetch_userinfo", fake_userinfo)
    payload = auth_mod._signer.dumps({"state": "s", "verifier": "v", "next": "/", "provider": "google"})
    client_mock = AsyncMock()
    client_mock.authorize_access_token = AsyncMock(return_value={"access_token": "t"})
    with patch.object(auth_mod.oauth, "create_client", return_value=client_mock):
        resp = await auth_client.get(
            "/api/auth/google/callback?state=s&code=c",
            cookies={"oauth_tx": payload},
            headers={"user-agent": "test-ua"},
            follow_redirects=False,
        )
    assert resp.status_code == 302
    uid = await aconn.fetchval("SELECT user_id FROM users WHERE email='field@x'")
    row = await aconn.fetchrow(
        "SELECT user_id, actor_id, kind, provider, user_agent, meta FROM login_events "
        "WHERE user_id=$1 AND kind='login'",
        uid,
    )
    assert row is not None
    assert row["user_id"] == uid
    assert row["actor_id"] == uid
    assert row["provider"] == "google"
    assert row["user_agent"] == "test-ua"
    assert row["meta"] is None
    sid = await aconn.fetchval("SELECT sid FROM sessions WHERE user_id=$1", uid)
    assert sid is not None
    assert f"sid={sid}" in resp.headers.get("set-cookie", "")


@pytest.mark.asyncio
async def test_repeat_login_skips_account_created(auth_client, aconn, monkeypatch):
    """Second login from the same provider only emits ``login``, not account_created."""
    uid = (await aconn.fetchrow("INSERT INTO users (email) VALUES ('repeat@x') RETURNING user_id"))["user_id"]
    await aconn.execute(
        "INSERT INTO oauth_identities (provider, provider_sub, user_id, email_at_link) "
        "VALUES ('google', 'r-sub', $1, 'repeat@x')",
        uid,
    )

    from api.routers import auth as auth_mod

    async def fake_userinfo(client, token, provider):
        return {"sub": "r-sub", "email": "repeat@x", "email_verified": True, "name": None, "avatar_url": None}

    monkeypatch.setattr(auth_mod, "_fetch_userinfo", fake_userinfo)
    payload = auth_mod._signer.dumps({"state": "s", "verifier": "v", "next": "/", "provider": "google"})
    client_mock = AsyncMock()
    client_mock.authorize_access_token = AsyncMock(return_value={"access_token": "t"})
    with patch.object(auth_mod.oauth, "create_client", return_value=client_mock):
        await auth_client.get(
            "/api/auth/google/callback?state=s&code=c",
            cookies={"oauth_tx": payload},
            follow_redirects=False,
        )
    kinds = [
        r["kind"] for r in await aconn.fetch("SELECT kind FROM login_events WHERE user_id=$1 ORDER BY event_id", uid)
    ]
    assert "account_created" not in kinds
    assert "login" in kinds


@pytest.mark.asyncio
async def test_bad_state_records_login_failed(auth_client, aconn):
    """Missing tx cookie + mismatched state → login_failed audit row."""
    resp = await auth_client.get(
        "/api/auth/google/callback?code=abc&state=mismatch",
        cookies={"oauth_tx": "garbage"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    row = await aconn.fetchrow(
        "SELECT kind, provider, meta::text AS meta FROM login_events "
        "WHERE kind='login_failed' ORDER BY event_id DESC LIMIT 1"
    )
    assert row is not None
    assert row["provider"] == "google"
    assert '"state"' in row["meta"]


@pytest.mark.asyncio
async def test_unverified_email_records_login_failed(auth_client, aconn, monkeypatch):
    from api.routers import auth as auth_mod

    async def fake_userinfo(client, token, provider):
        return {"sub": "u", "email": "u@x", "email_verified": False, "name": None, "avatar_url": None}

    monkeypatch.setattr(auth_mod, "_fetch_userinfo", fake_userinfo)
    payload = auth_mod._signer.dumps({"state": "s", "verifier": "v", "next": "/", "provider": "google"})
    client_mock = AsyncMock()
    client_mock.authorize_access_token = AsyncMock(return_value={"access_token": "t"})
    with patch.object(auth_mod.oauth, "create_client", return_value=client_mock):
        await auth_client.get(
            "/api/auth/google/callback?state=s&code=c",
            cookies={"oauth_tx": payload},
            follow_redirects=False,
        )
    row = await aconn.fetchrow(
        "SELECT meta::text AS meta FROM login_events WHERE kind='login_failed' ORDER BY event_id DESC LIMIT 1"
    )
    assert row is not None
    assert '"unverified_email"' in row["meta"]


@pytest.mark.asyncio
async def test_logout_when_anonymous_still_204(auth_client):
    """A user whose cookie already expired/cleared should not see a 401
    on POST /api/auth/logout — the endpoint is idempotent."""
    resp = await auth_client.post("/api/auth/logout", headers={"Origin": "http://test"})
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_logout_deletes_session(auth_client, aconn):
    from datetime import timedelta
    from datetime import timezone as tz

    uid = (await aconn.fetchrow("INSERT INTO users (email) VALUES ('x@x') RETURNING user_id"))["user_id"]
    sid = "test-sid"
    await aconn.execute(
        "INSERT INTO sessions (sid, user_id, expires_at) VALUES ($1, $2, $3)",
        sid,
        uid,
        datetime.now(tz.utc) + timedelta(days=30),
    )
    resp = await auth_client.post(
        "/api/auth/logout",
        cookies={"sid": sid},
        headers={"Origin": "http://test"},
    )
    assert resp.status_code == 204
    n = await aconn.fetchval("SELECT count(*) FROM sessions WHERE sid=$1", sid)
    assert n == 0


@pytest.mark.asyncio
async def test_real_login_then_callback_does_not_raise_duplicate_code_verifier(auth_client, aconn, monkeypatch):
    """Regression test for a real bug that broke every production login: the
    callback passed ``code_verifier`` explicitly to ``authorize_access_token``,
    but Authlib's own ``_format_state_params`` also injects ``code_verifier``
    from the Starlette session state that ``authorize_redirect`` (in /login)
    already stored there — passing both raised ``TypeError: got multiple
    values for keyword argument 'code_verifier'`` on every real attempt.

    Unlike the other tests in this file, this one does NOT mock
    ``authorize_access_token`` itself (that would mock away the exact bug).
    It drives the real /login endpoint first so the session is populated for
    real, then only stubs the network-bound token fetch one level deeper.
    Uses github (no OIDC discovery network call needed for authorize_redirect,
    unlike google's server_metadata_url config).
    """
    from api.routers import auth as auth_mod

    login_resp = await auth_client.get("/api/auth/github/login", follow_redirects=False)
    assert login_resp.status_code == 302
    tx_cookie = login_resp.cookies.get("oauth_tx")
    assert tx_cookie
    tx = auth_mod._signer.loads(tx_cookie)

    async def fake_userinfo(client, token, provider):
        return {"sub": "real-sub", "email": "real@x", "email_verified": True, "name": "Real", "avatar_url": None}

    monkeypatch.setattr(auth_mod, "_fetch_userinfo", fake_userinfo)

    client = auth_mod.oauth.create_client("github")

    async def fake_fetch_access_token(**kwargs):
        return {"access_token": "tok"}

    monkeypatch.setattr(client, "fetch_access_token", fake_fetch_access_token)

    # oauth_tx + the Starlette session cookie both persist on auth_client's
    # own cookie jar from the /login response above — no need to pass them
    # explicitly (and doing so would duplicate the Cookie header).
    resp = await auth_client.get(
        f"/api/auth/github/callback?state={tx['state']}&code=c",
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == "/"
    assert "error=" not in resp.headers["location"]
