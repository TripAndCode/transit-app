import os

import asyncpg
import httpx
import pytest
from httpx import ASGITransport

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://localhost/transit")


@pytest.fixture
async def ask_app(apply_schema):
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
async def ask_client(ask_app):
    app, agency_id = ask_app
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, agency_id


@pytest.mark.asyncio
async def test_query_endpoint_ranking_empty(ask_client):
    client, agency_id = ask_client
    payload = {"query_type": "ranking", "unknown": False, "limit": 5}
    resp = await client.post(f"/api/{agency_id}/query", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert "answer" in data
    assert "rows" in data
    assert isinstance(data["rows"], list)


@pytest.mark.asyncio
async def test_query_endpoint_unknown_agency(ask_client):
    client, _ = ask_client
    payload = {"query_type": "ranking", "unknown": False}
    resp = await client.post("/api/99999/query", json=payload)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_ask_endpoint_returns_answer(ask_client, monkeypatch):
    client, agency_id = ask_client
    from pipeline.query import intent as intent_mod

    async def mock_classify(question, model="llama3.2"):
        return {
            "query_type": "ranking",
            "unknown": False,
            "route": None,
            "route_name": None,
            "service": None,
            "dow": None,
            "dow_group": None,
            "date": None,
            "stop_name": None,
            "time_band": None,
            "trend_direction": "any",
            "compare_polarity": "any",
            "sort_order": "desc",
            "limit": 5,
        }

    monkeypatch.setattr(intent_mod, "classify_intent", mock_classify)
    resp = await client.post(
        f"/api/{agency_id}/ask",
        json={"question": "一番遅れている路線は？"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "answer" in data
    assert "intent" in data


@pytest.mark.asyncio
async def test_ask_endpoint_unknown_agency(ask_client):
    client, _ = ask_client
    resp = await client.post("/api/99999/ask", json={"question": "test"})
    assert resp.status_code == 404
