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
    """Stuff env vars + reload api.routers.auth so module-level reads pick them up.

    ADMIN_EMAILS is built at import-time as a frozen set; tests that need
    a different set monkeypatch ``auth_mod.ADMIN_EMAILS`` directly after import.
    """
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "g")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "gs")
    monkeypatch.setenv("GITHUB_CLIENT_ID", "h")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "hs")
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://test")
    monkeypatch.setenv("ADMIN_EMAILS", "")
    monkeypatch.setenv("SESSION_SIGNING_KEY", "test-signing-key")
    import importlib

    import api.routers.auth as auth_mod

    importlib.reload(auth_mod)
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
