import os
from datetime import datetime, timedelta, timezone

import asyncpg
import httpx
import pytest
from httpx import ASGITransport

from tests.conftest import TEST_ORIGIN

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://localhost/transit")


@pytest.fixture
async def app_client(apply_schema):
    from api.main import app

    pool = await asyncpg.create_pool(DATABASE_URL)
    app.state.pool = pool
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
    await pool.close()


@pytest.mark.asyncio
async def test_health_endpoint(app_client):
    resp = await app_client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def _seed_admin(conn) -> str:
    """Insert an admin user + session, return the sid cookie value."""
    uid = (
        await conn.fetchrow(
            "INSERT INTO users (email, role) VALUES ($1, 'admin') RETURNING user_id",
            f"admin{datetime.now().timestamp()}@x",
        )
    )["user_id"]
    sid = f"sid-{uid}-{datetime.now().timestamp()}"
    await conn.execute(
        "INSERT INTO sessions (sid, user_id, expires_at) VALUES ($1, $2, $3)",
        sid,
        uid,
        datetime.now(timezone.utc) + timedelta(days=1),
    )
    return sid


@pytest.fixture
async def agencies_client(apply_schema):
    from api.main import app

    pool = await asyncpg.create_pool(DATABASE_URL)
    app.state.pool = pool
    truncate_sql = (
        "TRUNCATE agencies, updates, static_stops, static_stop_times, "
        "static_trips, static_routes, static_calendar_dates, "
        "agg_route_stats, agg_route_hour, agg_route_dow, "
        "agg_daily_trend, agg_stop_seq, rag_chunks, sessions, users CASCADE"
    )
    # Pre-truncate so each test starts from a known-empty state — otherwise
    # data left over from `make seed-agencies` (or a parallel session) would
    # break test_list_agencies_empty's `[] == response` assertion.
    async with pool.acquire() as conn:
        await conn.execute(truncate_sql)
        admin_sid = await _seed_admin(conn)
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # creating agencies is admin-only (the feed_url is an SSRF sink), so every
        # test that creates one passes this admin session cookie.
        yield client, admin_sid
        async with pool.acquire() as conn:
            await conn.execute(truncate_sql)
    await pool.close()


@pytest.mark.asyncio
async def test_list_agencies_empty(agencies_client):
    client, _ = agencies_client
    resp = await client.get("/api/agencies")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_create_agency(agencies_client):
    client, sid = agencies_client
    payload = {"agency_name": "Aomori Bus", "feed_url": "http://aomori.example.com"}
    resp = await client.post("/api/agencies", json=payload, headers={"Origin": TEST_ORIGIN}, cookies={"sid": sid})
    assert resp.status_code == 201
    data = resp.json()
    assert "agency_id" in data
    assert data["agency_name"] == "Aomori Bus"
    assert data["static_url"] is None


@pytest.mark.asyncio
async def test_create_agency_requires_admin(agencies_client):
    """No admin session → the route is locked (the feed_url is a server-side fetch sink)."""
    client, _ = agencies_client
    resp = await client.post(
        "/api/agencies",
        json={"agency_name": "nope", "feed_url": "http://x.example.com"},
        headers={"Origin": TEST_ORIGIN},
    )
    assert resp.status_code in (401, 403)
    listing = await client.get("/api/agencies")
    assert all(a["agency_name"] != "nope" for a in listing.json())


@pytest.mark.asyncio
async def test_get_agency(agencies_client):
    client, sid = agencies_client
    payload = {"agency_name": "Test Agency", "feed_url": "http://test2.example.com"}
    create_resp = await client.post(
        "/api/agencies", json=payload, headers={"Origin": TEST_ORIGIN}, cookies={"sid": sid}
    )
    aid = create_resp.json()["agency_id"]
    resp = await client.get(f"/api/agencies/{aid}")
    assert resp.status_code == 200
    assert resp.json()["agency_id"] == aid


@pytest.mark.asyncio
async def test_get_agency_not_found(agencies_client):
    client, _ = agencies_client
    resp = await client.get("/api/agencies/99999")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_agencies_returns_multiple(agencies_client):
    client, sid = agencies_client
    for name, url in (("A", "http://a.example.com"), ("B", "http://b.example.com")):
        await client.post(
            "/api/agencies",
            json={"agency_name": name, "feed_url": url},
            headers={"Origin": TEST_ORIGIN},
            cookies={"sid": sid},
        )
    resp = await client.get("/api/agencies")
    assert resp.status_code == 200
    assert len(resp.json()) >= 2


@pytest.mark.asyncio
async def test_create_agency_rejects_cross_origin(agencies_client):
    """An admin with a cross-origin POST still fails csrf_guard (403)."""
    client, sid = agencies_client
    resp = await client.post(
        "/api/agencies",
        json={"agency_name": "evil", "feed_url": "http://evil.example.com/feed.pb"},
        headers={"Origin": "https://evil.example.com"},
        cookies={"sid": sid},
    )
    assert resp.status_code == 403
    listing = await client.get("/api/agencies")
    assert all(a["agency_name"] != "evil" for a in listing.json())


@pytest.mark.asyncio
async def test_create_agency_rejects_path_suffixed_origin(agencies_client):
    """Origin with a path is RFC-invalid and must not collapse to the trusted base."""
    client, sid = agencies_client
    resp = await client.post(
        "/api/agencies",
        json={"agency_name": "evil", "feed_url": "http://evil.example.com/feed.pb"},
        headers={"Origin": f"{TEST_ORIGIN}/.evil"},
        cookies={"sid": sid},
    )
    assert resp.status_code == 403


@pytest.mark.parametrize(
    "bad_origin",
    [
        f"{TEST_ORIGIN}?evil=1",  # query in Origin
        "http://user:pass@test",  # userinfo in Origin
    ],
)
@pytest.mark.asyncio
async def test_create_agency_rejects_malformed_origin(agencies_client, bad_origin):
    """Origin headers carrying query / userinfo are RFC-invalid → 403."""
    client, sid = agencies_client
    resp = await client.post(
        "/api/agencies",
        json={"agency_name": "x", "feed_url": "https://x/y"},
        headers={"Origin": bad_origin},
        cookies={"sid": sid},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_create_agency_accepts_uppercase_scheme(agencies_client):
    """Schemes are case-insensitive per RFC 3986; uppercase Origin normalises."""
    client, sid = agencies_client
    resp = await client.post(
        "/api/agencies",
        json={"agency_name": "Caps", "feed_url": "https://caps/feed"},
        headers={"Origin": TEST_ORIGIN.upper()},
        cookies={"sid": sid},
    )
    assert resp.status_code == 201
