import os

import asyncpg
import httpx
import pytest
from httpx import ASGITransport

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


@pytest.fixture
async def agencies_client(apply_schema):
    from api.main import app

    pool = await asyncpg.create_pool(DATABASE_URL)
    app.state.pool = pool
    truncate_sql = (
        "TRUNCATE agencies, updates, static_stops, static_stop_times, "
        "static_trips, static_routes, static_calendar_dates, "
        "agg_route_stats, agg_route_hour, agg_route_dow, "
        "agg_daily_trend, agg_stop_seq, rag_chunks CASCADE"
    )
    # Pre-truncate so each test starts from a known-empty state — otherwise
    # data left over from `make seed-agencies` (or a parallel session) would
    # break test_list_agencies_empty's `[] == response` assertion.
    async with pool.acquire() as conn:
        await conn.execute(truncate_sql)
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
        async with pool.acquire() as conn:
            await conn.execute(truncate_sql)
    await pool.close()


@pytest.mark.asyncio
async def test_list_agencies_empty(agencies_client):
    resp = await agencies_client.get("/api/agencies")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_create_agency(agencies_client):
    payload = {"agency_name": "Aomori Bus", "feed_url": "http://aomori.example.com"}
    resp = await agencies_client.post("/api/agencies", json=payload, headers={"Origin": "http://test"})
    assert resp.status_code == 201
    data = resp.json()
    assert "agency_id" in data
    assert data["agency_name"] == "Aomori Bus"
    assert data["static_url"] is None


@pytest.mark.asyncio
async def test_get_agency(agencies_client):
    payload = {"agency_name": "Test Agency", "feed_url": "http://test2.example.com"}
    create_resp = await agencies_client.post("/api/agencies", json=payload, headers={"Origin": "http://test"})
    aid = create_resp.json()["agency_id"]
    resp = await agencies_client.get(f"/api/agencies/{aid}")
    assert resp.status_code == 200
    assert resp.json()["agency_id"] == aid


@pytest.mark.asyncio
async def test_get_agency_not_found(agencies_client):
    resp = await agencies_client.get("/api/agencies/99999")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_agencies_returns_multiple(agencies_client):
    await agencies_client.post(
        "/api/agencies",
        json={"agency_name": "A", "feed_url": "http://a.example.com"},
        headers={"Origin": "http://test"},
    )
    await agencies_client.post(
        "/api/agencies",
        json={"agency_name": "B", "feed_url": "http://b.example.com"},
        headers={"Origin": "http://test"},
    )
    resp = await agencies_client.get("/api/agencies")
    assert resp.status_code == 200
    assert len(resp.json()) >= 2


@pytest.mark.asyncio
async def test_create_agency_rejects_cross_origin(agencies_client):
    """Cross-origin POST without an allowlisted Origin returns 403 (csrf_guard)."""
    resp = await agencies_client.post(
        "/api/agencies",
        json={"agency_name": "evil", "feed_url": "http://evil.example.com/feed.pb"},
        headers={"Origin": "https://evil.example.com"},
    )
    assert resp.status_code == 403
