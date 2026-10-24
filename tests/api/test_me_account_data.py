"""Self-serve account deletion and data export on /api/me."""

import httpx
import pytest
from httpx import ASGITransport

from api.middleware.ratelimit import limiter
from tests.conftest import TEST_ORIGIN, _test_pool
from tests.fixtures.users import seed_session


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


@pytest.mark.asyncio
async def test_deleting_the_account_removes_it_and_clears_the_cookie(me_client, aconn):
    sid, uid, _ = await seed_session(aconn, email="del@x")
    r = await me_client.request(
        "DELETE", "/api/me", json={"confirm_email": " DEL@x "}, cookies={"sid": sid}, headers={"Origin": TEST_ORIGIN}
    )
    assert r.status_code == 204
    assert "sid=" in r.headers.get("set-cookie", "")
    assert await aconn.fetchval("SELECT count(*) FROM users WHERE user_id = $1", uid) == 0


@pytest.mark.asyncio
async def test_a_mismatched_confirmation_deletes_nothing(me_client, aconn):
    sid, uid, _ = await seed_session(aconn, email="keep@x")
    r = await me_client.request(
        "DELETE", "/api/me", json={"confirm_email": "other@x"}, cookies={"sid": sid}, headers={"Origin": TEST_ORIGIN}
    )
    assert r.status_code == 400
    assert r.json()["detail"] == "confirmation_mismatch"
    assert await aconn.fetchval("SELECT count(*) FROM users WHERE user_id = $1", uid) == 1


@pytest.mark.asyncio
async def test_the_last_admin_is_refused(me_client, aconn):
    sid = (await seed_session(aconn, email="admin@x", role="admin")).sid
    r = await me_client.request(
        "DELETE", "/api/me", json={"confirm_email": "admin@x"}, cookies={"sid": sid}, headers={"Origin": TEST_ORIGIN}
    )
    assert r.status_code == 409
    assert r.json()["detail"] == "last_admin"


@pytest.mark.asyncio
async def test_deletion_requires_a_same_origin_request(me_client, aconn):
    sid = (await seed_session(aconn, email="csrf@x")).sid
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
    sid, uid, _ = await seed_session(aconn, email="exp@x", role="admin")
    await aconn.execute(
        "INSERT INTO admin_audit (actor_id, action, target_type, ip) VALUES ($1, 'flag.set', 'flag', '10.0.0.9')", uid
    )
    r = await me_client.get("/api/me/export", cookies={"sid": sid})
    assert r.status_code == 200
    assert r.headers["content-disposition"].startswith('attachment; filename="transit-app-export-')
    body = r.json()
    assert body["profile"]["email"] == "exp@x"
    assert [(a["action"], a["ip"]) for a in body["admin_actions"]] == [("flag.set", "10.0.0.9")]
    assert "sid_hash" not in r.text


@pytest.mark.asyncio
async def test_the_export_needs_a_session(me_client):
    r = await me_client.get("/api/me/export")
    assert r.status_code == 401
