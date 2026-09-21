"""Middleware credential lookups resolve hashes, never raw bearer tokens.

Both suites drive the real middleware over a mocked asyncpg pool, so they
assert the SQL parameter that leaves the process as well as the decision
made from the row that comes back. No database is involved.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from api.middleware.auth import APIKeyMiddleware
from api.middleware.session import SESSION_COOKIE_NAME, SessionMiddleware
from api.security import token_hash

NOW = datetime.now(timezone.utc)


def _app(middleware, fetchrow_result):
    app = FastAPI()
    app.add_middleware(middleware)
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value=fetchrow_result)
    pool.execute = AsyncMock(return_value=None)
    app.state.pool = pool

    @app.get("/probe")
    async def probe(request: Request):
        user = getattr(request.state, "user", None)
        return {"tier": getattr(request.state, "tier", None), "user_id": user.user_id if user else None}

    return app, pool


# --- API keys -------------------------------------------------------------


def _key_row(*, tier="pro", revoked_at=None, expires_at=None):
    return {"tier": tier, "revoked_at": revoked_at, "expires_at": expires_at}


def test_no_api_key_is_free_tier_without_a_lookup():
    app, pool = _app(APIKeyMiddleware, None)
    resp = TestClient(app).get("/probe")
    assert resp.status_code == 200
    assert resp.json()["tier"] == "free"
    pool.fetchrow.assert_not_called()


def test_active_api_key_is_looked_up_by_hash_not_by_raw_key():
    app, pool = _app(APIKeyMiddleware, _key_row())
    resp = TestClient(app).get("/probe", headers={"X-API-Key": "raw-secret-key"})
    assert resp.status_code == 200
    assert resp.json()["tier"] == "pro"
    sql, param = pool.fetchrow.call_args.args[0], pool.fetchrow.call_args.args[1]
    assert param == token_hash("raw-secret-key")
    assert "raw-secret-key" not in sql
    assert "key_hash" in sql


def test_unknown_api_key_is_rejected():
    app, _ = _app(APIKeyMiddleware, None)
    resp = TestClient(app).get("/probe", headers={"X-API-Key": "nope"})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid API key"


def test_revoked_api_key_is_rejected():
    app, _ = _app(APIKeyMiddleware, _key_row(revoked_at=NOW - timedelta(days=1)))
    resp = TestClient(app).get("/probe", headers={"X-API-Key": "revoked"})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid API key"


def test_expired_api_key_is_rejected():
    app, _ = _app(APIKeyMiddleware, _key_row(expires_at=NOW - timedelta(seconds=1)))
    resp = TestClient(app).get("/probe", headers={"X-API-Key": "stale"})
    assert resp.status_code == 401


def test_api_key_with_future_expiry_is_accepted():
    app, _ = _app(APIKeyMiddleware, _key_row(expires_at=NOW + timedelta(days=30)))
    resp = TestClient(app).get("/probe", headers={"X-API-Key": "fresh"})
    assert resp.status_code == 200
    assert resp.json()["tier"] == "pro"


# --- Sessions -------------------------------------------------------------


def _session_row(*, expires_at, suspended_at=None):
    return {
        "expires_at": expires_at,
        "user_id": 7,
        "email": "u@example.test",
        "name": None,
        "avatar_url": None,
        "role": "user",
        "suspended_at": suspended_at,
        "llm_approved": False,
    }


def test_active_session_is_looked_up_by_hash_not_by_raw_cookie():
    app, pool = _app(SessionMiddleware, _session_row(expires_at=NOW + timedelta(days=30)))
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE_NAME, "raw-cookie-value")
    resp = client.get("/probe")
    assert resp.status_code == 200
    assert resp.json()["user_id"] == 7
    sql, param = pool.fetchrow.call_args.args[0], pool.fetchrow.call_args.args[1]
    assert param == token_hash("raw-cookie-value")
    assert "raw-cookie-value" not in sql
    assert "sid_hash" in sql


def test_unknown_session_cookie_is_cleared():
    app, pool = _app(SessionMiddleware, None)
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE_NAME, "ghost")
    resp = client.get("/probe")
    assert resp.json()["user_id"] is None
    assert "Max-Age=0" in resp.headers.get("set-cookie", "")
    pool.execute.assert_not_called()


def test_expired_session_is_deleted_by_hash():
    app, pool = _app(SessionMiddleware, _session_row(expires_at=NOW - timedelta(seconds=1)))
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE_NAME, "stale-cookie")
    resp = client.get("/probe")
    assert resp.json()["user_id"] is None
    assert "Max-Age=0" in resp.headers.get("set-cookie", "")
    sql, param = pool.execute.call_args.args[0], pool.execute.call_args.args[1]
    assert sql.startswith("DELETE FROM sessions")
    assert param == token_hash("stale-cookie")


def test_suspended_user_session_is_deleted_by_hash():
    row = _session_row(expires_at=NOW + timedelta(days=30), suspended_at=NOW)
    app, pool = _app(SessionMiddleware, row)
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE_NAME, "suspended-cookie")
    resp = client.get("/probe")
    assert resp.json()["user_id"] is None
    assert pool.execute.call_args.args[1] == token_hash("suspended-cookie")


def test_last_seen_touch_uses_the_hash():
    """The throttle and the UPDATE both key on the digest, so no raw session
    id is retained in process memory after the lookup."""
    from api.middleware import session as session_mod

    session_mod._TOUCH_THROTTLE.clear()
    app, pool = _app(SessionMiddleware, _session_row(expires_at=NOW + timedelta(days=30)))
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE_NAME, "touch-me")
    client.get("/probe")
    sql, param = pool.execute.call_args.args[0], pool.execute.call_args.args[1]
    assert "last_seen_at" in sql
    assert param == token_hash("touch-me")
    assert list(session_mod._TOUCH_THROTTLE) == [token_hash("touch-me")]
    session_mod._TOUCH_THROTTLE.clear()
