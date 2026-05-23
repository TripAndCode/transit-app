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
async def test_ask_endpoint_returns_answer(ask_client, monkeypatch):
    """v2 ask uses tool-use; mock chat_with_tools so the test is offline."""
    client, agency_id = ask_client

    async def mock_chat(question, ctx, conn, agency_id, model="x"):
        return {
            "answer": "テスト回答",
            "tool_call": {"name": "top_n", "arguments": {"metric": "avg_delay", "n": 5}},
            "result": {
                "kind": "table",
                "summary_jp": "テスト",
                "rows": [],
                "columns": ["route_code", "service_type"],
                "series": [],
                "pairs": [],
            },
        }

    # Patch the symbol in the module that imports it (api.routers.ask)
    monkeypatch.setattr("api.routers.ask.chat_with_tools", mock_chat)
    resp = await client.post(
        f"/api/{agency_id}/ask",
        json={"question": "一番遅れている路線は？"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["answer"] == "テスト回答"
    assert data["tool_call"]["name"] == "top_n"
    assert data["result"]["kind"] == "table"
    assert data["ctx"]["from"]
    assert data["ctx"]["to"]


@pytest.mark.asyncio
async def test_ask_endpoint_unknown_agency(ask_client):
    client, _ = ask_client
    resp = await client.post("/api/99999/ask", json={"question": "test"})
    assert resp.status_code == 404
