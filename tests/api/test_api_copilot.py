import os

import asyncpg
import httpx
import pytest
from httpx import ASGITransport

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://localhost/transit")


@pytest.fixture
async def copilot_app(apply_schema):
    """Boot the FastAPI app against a real pool with one seeded agency.

    Mirrors ``tests/api/test_api_ask.py``'s ``ask_app`` fixture: the plan's
    Task 4 test snippet posts straight to a hardcoded ``/api/1/copilot/insight``
    with no DB wiring, but ``copilot_insight``'s ``agency_id: int =
    Depends(get_agency)`` always resolves against ``app.state.pool`` and a
    real row — there is no code path that skips it, mocked LLM call or not.
    """
    from api.main import app

    pool = await asyncpg.create_pool(DATABASE_URL)
    app.state.pool = pool
    row = await pool.fetchrow(
        "INSERT INTO agencies (agency_name, feed_url) VALUES ($1, $2) RETURNING agency_id",
        "Test Agency",
        "http://test.example.com",
    )
    agency_id = row["agency_id"]
    yield app, agency_id
    async with pool.acquire() as conn:
        await conn.execute(
            "TRUNCATE agencies, updates, static_stops, static_stop_times, "
            "static_trips, static_routes, static_calendar_dates, "
            "agg_route_stats, agg_route_hour, agg_route_dow, "
            "agg_daily_trend, agg_stop_seq, rag_chunks CASCADE"
        )
    await pool.close()


@pytest.fixture
async def copilot_client(copilot_app):
    app, agency_id = copilot_app
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, agency_id


@pytest.mark.asyncio
async def test_copilot_insight_returns_rendered_text(copilot_client, monkeypatch):
    client, agency_id = copilot_client

    async def fake_insight(tab, filters, view_payload, *, locale="ja"):
        return {"text": "Route 12 is delayed.", "cite": "Overview · 1 sample", "low_confidence": False}

    monkeypatch.setattr("api.routers.copilot.generate_proactive_insight", fake_insight)
    resp = await client.post(
        f"/api/{agency_id}/copilot/insight",
        json={"tab": "overview", "filters": {}, "view_payload": {"headline": {"samples": 1}}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"text": "Route 12 is delayed.", "cite": "Overview · 1 sample", "low_confidence": False}


@pytest.mark.asyncio
async def test_copilot_insight_rejects_empty_payload(copilot_client, monkeypatch):
    client, agency_id = copilot_client

    async def fake_insight(tab, filters, view_payload, *, locale="ja"):
        from pipeline.query.copilot import NoInsightAvailable

        raise NoInsightAvailable("empty")

    monkeypatch.setattr("api.routers.copilot.generate_proactive_insight", fake_insight)
    resp = await client.post(
        f"/api/{agency_id}/copilot/insight", json={"tab": "overview", "filters": {}, "view_payload": {}}
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_copilot_insight_enforces_anon_quota(copilot_client, monkeypatch):
    client, agency_id = copilot_client

    async def fake_insight(tab, filters, view_payload, *, locale="ja"):
        return {"text": "x", "cite": "y", "low_confidence": False}

    monkeypatch.setattr("api.routers.copilot.generate_proactive_insight", fake_insight)
    monkeypatch.setenv("COPILOT_ANON_DAILY_LIMIT", "0")
    from api.middleware.ratelimit import reset_anon_quota_for_tests

    reset_anon_quota_for_tests()
    resp = await client.post(
        f"/api/{agency_id}/copilot/insight",
        json={"tab": "overview", "filters": {}, "view_payload": {"headline": {"samples": 1}}},
    )
    assert resp.status_code == 429
    assert resp.json()["code"] == "copilot_anon_quota_exceeded"
