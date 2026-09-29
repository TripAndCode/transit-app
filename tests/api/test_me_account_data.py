"""Self-serve account deletion and data export on /api/me."""

from datetime import datetime, timedelta, timezone

import httpx
import pytest
from httpx import ASGITransport

from api.middleware.ratelimit import limiter
from api.security import token_hash
from tests.conftest import TEST_ORIGIN, _test_pool


@pytest.fixture(autouse=True)
def _fresh_rate_limits():
    limiter.reset()


@pytest.fixture
async def me_client(apply_schema):
    from api.main import app

    pool = await _test_pool()
    app.state.pool = pool
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    await pool.close()


async def _signed_in(conn, email, *, role="user"):
    uid = await conn.fetchval("INSERT INTO users (email, role) VALUES ($1, $2) RETURNING user_id", email, role)
    sid = f"sid-data-{uid}"
    await conn.execute(
        "INSERT INTO sessions (sid_hash, user_id, expires_at) VALUES ($1, $2, $3)",
        token_hash(sid),
        uid,
        datetime.now(timezone.utc) + timedelta(days=1),
    )
    return sid, uid


@pytest.mark.asyncio
async def test_deleting_the_account_removes_it_and_clears_the_cookie(me_client, aconn):
    sid, uid = await _signed_in(aconn, "del@x")
    r = await me_client.request(
        "DELETE", "/api/me", json={"confirm_email": " DEL@x "}, cookies={"sid": sid}, headers={"Origin": TEST_ORIGIN}
    )
    assert r.status_code == 204
    assert "sid=" in r.headers.get("set-cookie", "")
    assert await aconn.fetchval("SELECT count(*) FROM users WHERE user_id = $1", uid) == 0


@pytest.mark.asyncio
async def test_a_mismatched_confirmation_deletes_nothing(me_client, aconn):
    sid, uid = await _signed_in(aconn, "keep@x")
    r = await me_client.request(
        "DELETE", "/api/me", json={"confirm_email": "other@x"}, cookies={"sid": sid}, headers={"Origin": TEST_ORIGIN}
    )
    assert r.status_code == 400
    assert r.json()["detail"] == "confirmation_mismatch"
    assert await aconn.fetchval("SELECT count(*) FROM users WHERE user_id = $1", uid) == 1


@pytest.mark.asyncio
async def test_the_last_admin_is_refused(me_client, aconn):
    sid, _ = await _signed_in(aconn, "admin@x", role="admin")
    r = await me_client.request(
        "DELETE", "/api/me", json={"confirm_email": "admin@x"}, cookies={"sid": sid}, headers={"Origin": TEST_ORIGIN}
    )
    assert r.status_code == 409
    assert r.json()["detail"] == "last_admin"


@pytest.mark.asyncio
async def test_deletion_requires_a_same_origin_request(me_client, aconn):
    sid, _ = await _signed_in(aconn, "csrf@x")
    r = await me_client.request(
        "DELETE",
        "/api/me",
        json={"confirm_email": "csrf@x"},
        cookies={"sid": sid},
        headers={"Origin": "http://evil.example"},
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_the_export_downloads_the_users_data(me_client, aconn):
    sid, _ = await _signed_in(aconn, "exp@x")
    r = await me_client.get("/api/me/export", cookies={"sid": sid})
    assert r.status_code == 200
    assert r.headers["content-disposition"].startswith('attachment; filename="transit-app-export-')
    body = r.json()
    assert body["profile"]["email"] == "exp@x"
    assert "sid_hash" not in r.text


@pytest.mark.asyncio
async def test_the_export_needs_a_session(me_client):
    r = await me_client.get("/api/me/export")
    assert r.status_code == 401
