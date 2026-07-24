"""Coverage for the break-glass local-admin login: startup seeding
(seed_local_admin) and the POST /api/auth/local/login endpoint. Mirrors
test_oauth_flow.py's fixture shape but never touches Authlib/OAuth."""

import os

import asyncpg
import httpx
import pytest
from httpx import ASGITransport

from api.middleware.ratelimit import limiter

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://localhost/transit")


@pytest.fixture(autouse=True)
def _set_local_admin_env(monkeypatch):
    """Unlike test_oauth_flow.py's ADMIN_EMAILS (frozen into a module-level
    set at import time), local_admin_enabled()/DEFAULT_ADMIN_* are read live
    from os.environ on every call — so plain monkeypatch.setenv is enough,
    no importlib.reload needed. Deliberately skip reloading api.routers.auth
    here: reload() re-runs its `@limiter.limit(...)` decorator, registering
    a duplicate limit entry for the same route on every single test (the
    route object it creates is never remounted — api.main mounted the
    original one at startup — but the duplicate registration still corrupts
    slowapi's bookkeeping for the one that IS mounted), which was silently
    lowering the effective rate limit threshold test-by-test.
    """
    monkeypatch.setenv("DEFAULT_ADMIN_USERNAME", "root@local")
    monkeypatch.setenv("DEFAULT_ADMIN_PASSWORD", "correct-horse-battery-staple")
    limiter.reset()
    yield


@pytest.fixture
async def local_client(apply_schema):
    from api.main import app

    pool = await asyncpg.create_pool(DATABASE_URL)
    app.state.pool = pool
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        c.pool = pool  # exposed so tests can call seed_local_admin(c.pool) directly
        yield c
    await pool.close()


async def _seed(client):
    from api.routers import auth as auth_mod

    await auth_mod.seed_local_admin(client.pool)


@pytest.mark.asyncio
async def test_seed_creates_the_local_admin_as_admin_role(local_client, aconn):
    await _seed(local_client)
    row = await aconn.fetchrow("SELECT role, password_hash FROM users WHERE email='root@local'")
    assert row is not None
    assert row["role"] == "admin"
    assert row["password_hash"] is not None


@pytest.mark.asyncio
async def test_seed_is_a_noop_when_env_unset(local_client, aconn, monkeypatch):
    monkeypatch.delenv("DEFAULT_ADMIN_USERNAME", raising=False)
    monkeypatch.delenv("DEFAULT_ADMIN_PASSWORD", raising=False)
    await _seed(local_client)
    n = await aconn.fetchval("SELECT count(*) FROM users WHERE email='root@local'")
    assert n == 0


@pytest.mark.asyncio
async def test_seed_never_demotes_the_break_glass_account(local_client, aconn):
    """Re-seeding (e.g. every server restart) must keep forcing role=admin,
    even if something else demoted it in between."""
    await _seed(local_client)
    await aconn.execute("UPDATE users SET role='user' WHERE email='root@local'")
    await _seed(local_client)
    role = await aconn.fetchval("SELECT role FROM users WHERE email='root@local'")
    assert role == "admin"


@pytest.mark.asyncio
async def test_seed_refuses_to_take_over_an_oauth_linked_account(local_client, aconn):
    """DEFAULT_ADMIN_USERNAME matching a real OAuth-provisioned user's email
    must not silently grant that account a password login + admin role."""
    row = await aconn.fetchrow(
        "INSERT INTO users (email, name, role) VALUES ('root@local', 'Real SSO User', 'user') RETURNING user_id"
    )
    await aconn.execute(
        "INSERT INTO oauth_identities (provider, provider_sub, user_id, email_at_link) "
        "VALUES ('google', 'sub-123', $1, 'root@local')",
        row["user_id"],
    )
    await _seed(local_client)
    after = await aconn.fetchrow("SELECT role, password_hash FROM users WHERE email='root@local'")
    assert after["role"] == "user"
    assert after["password_hash"] is None


@pytest.mark.asyncio
async def test_seed_audits_promoting_a_pre_existing_non_oauth_account(local_client, aconn):
    """Seeding over an existing (non-OAuth) account that wasn't already
    admin must leave an audit trail of the role change."""
    row = await aconn.fetchrow(
        "INSERT INTO users (email, name, role) VALUES ('root@local', 'Pre-existing', 'user') RETURNING user_id"
    )
    await _seed(local_client)
    role = await aconn.fetchval("SELECT role FROM users WHERE user_id=$1", row["user_id"])
    assert role == "admin"
    event = await aconn.fetchrow(
        "SELECT kind, meta FROM login_events WHERE user_id=$1 ORDER BY created_at DESC LIMIT 1", row["user_id"]
    )
    assert event is not None
    assert event["kind"] == "role_changed"


@pytest.mark.asyncio
async def test_login_with_correct_credentials_sets_session_cookie(local_client, aconn):
    await _seed(local_client)
    resp = await local_client.post(
        "/api/auth/local/login",
        json={"username": "root@local", "password": "correct-horse-battery-staple"},
    )
    assert resp.status_code == 200
    assert "sid=" in resp.headers.get("set-cookie", "")
    uid = await aconn.fetchval("SELECT user_id FROM users WHERE email='root@local'")
    s = await aconn.fetchrow("SELECT sid FROM sessions WHERE user_id=$1", uid)
    assert s is not None
    kinds = [r["kind"] for r in await aconn.fetch("SELECT kind FROM login_events WHERE user_id=$1", uid)]
    assert "login" in kinds


@pytest.mark.asyncio
async def test_login_with_wrong_password_is_rejected_and_audited(local_client, aconn):
    await _seed(local_client)
    resp = await local_client.post(
        "/api/auth/local/login",
        json={"username": "root@local", "password": "not-the-password"},
    )
    assert resp.status_code == 401
    assert "set-cookie" not in resp.headers
    row = await aconn.fetchrow(
        "SELECT provider, meta::text AS meta FROM login_events WHERE kind='login_failed' ORDER BY event_id DESC LIMIT 1"
    )
    assert row is not None
    assert row["provider"] == "local"
    assert "bad_credentials" in row["meta"]


@pytest.mark.asyncio
async def test_login_with_unknown_username_gets_the_same_generic_error(local_client):
    """No user-enumeration: a nonexistent username gets the identical 401
    shape as a wrong password for a real one."""
    resp = await local_client.post(
        "/api/auth/local/login",
        json={"username": "nobody@nowhere", "password": "whatever"},
    )
    assert resp.status_code == 401
    assert resp.json() == {"error": "invalid_credentials"}


@pytest.mark.asyncio
async def test_login_returns_503_when_not_configured(local_client, monkeypatch):
    monkeypatch.delenv("DEFAULT_ADMIN_USERNAME", raising=False)
    monkeypatch.delenv("DEFAULT_ADMIN_PASSWORD", raising=False)
    resp = await local_client.post(
        "/api/auth/local/login",
        json={"username": "root@local", "password": "correct-horse-battery-staple"},
    )
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_login_is_rate_limited_after_repeated_attempts(local_client):
    """Rate-limited per IP — enough rapid attempts in a row must eventually
    be throttled (429), independent of whether the credentials are right or
    wrong. Doesn't assert the exact configured threshold (5/minute) since
    that's an implementation detail of the limits string; asserts the
    protection itself exists."""
    statuses = []
    for _ in range(20):
        resp = await local_client.post(
            "/api/auth/local/login",
            json={"username": "root@local", "password": "wrong"},
        )
        statuses.append(resp.status_code)
        if resp.status_code == 429:
            break
    assert 429 in statuses, f"never throttled after 20 attempts: {statuses}"
    assert all(s == 401 for s in statuses[:-1]), f"unexpected status before throttling: {statuses}"
