"""Tests for the v2 reports endpoints (live queries, no snapshots table)."""

import os

import asyncpg
import httpx
import pytest
from httpx import ASGITransport

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://localhost/transit")


@pytest.fixture
async def reports_app(apply_schema):
    from api.main import app

    pool = await asyncpg.create_pool(DATABASE_URL)
    app.state.pool = pool
    row = await pool.fetchrow(
        "INSERT INTO agencies (agency_name, feed_url) VALUES ($1, $2) RETURNING agency_id",
        "Reports Test Agency",
        "http://reports-test.example.com",
    )
    agency_id = row["agency_id"]
    yield app, agency_id, pool
    async with pool.acquire() as conn:
        await conn.execute(
            "TRUNCATE agencies, updates, static_stops, static_stop_times, "
            "static_trips, static_routes, static_calendar_dates, "
            "agg_route_stats, agg_route_hour, agg_route_dow, "
            "agg_daily_trend, agg_stop_seq, rag_chunks, api_keys CASCADE"
        )
    await pool.close()


@pytest.fixture
async def reports_client(reports_app):
    app, agency_id, pool = reports_app
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, agency_id, pool


@pytest.mark.asyncio
async def test_reports_list_returns_static_metadata(reports_client):
    """The list endpoint returns the canonical 8 report types regardless of data."""
    client, agency_id, _ = reports_client
    resp = await client.get(f"/api/{agency_id}/reports")
    assert resp.status_code == 200
    data = resp.json()
    types = {r["report_type"] for r in data}
    assert types == {
        "ranking",
        "ranking_best",
        "on_time",
        "worst_5min",
        "trend",
        "compare_ranking",
        "dow_weekend",
        "dow_weekday",
    }
    for r in data:
        assert "rendered_at" in r


@pytest.mark.asyncio
async def test_reports_get_unknown_type_returns_404(reports_client):
    client, agency_id, _ = reports_client
    resp = await client.get(f"/api/{agency_id}/reports/nonexistent_type")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_reports_get_ranking_with_seeded_updates(reports_client):
    """A live ranking query reads `updates` and renders text + rows.

    Compute requires HAVING COUNT(*) > 20, so seed 25 update rows for route 44
    spread across distinct trip_ids so the dedup CTE preserves them all.
    """
    from datetime import datetime

    client, agency_id, pool = reports_client
    today = "2026-05-01"
    async with pool.acquire() as conn:
        for i in range(25):
            await conn.execute(
                "INSERT INTO updates "
                "(agency_id, trip_id, route_code, service_type, scheduled_time, "
                " stop_sequence, dep_delay, captured_at, file_name) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)",
                agency_id,
                f"trip-{i}",
                "44",
                "平日",
                "10:00:00",
                1,
                300,
                datetime.fromisoformat(f"{today}T10:0{i % 10}:00"),
                f"test/{i}.pb",
            )
    # ctx must include the seeded date — use ?from=&to=
    resp = await client.get(f"/api/{agency_id}/reports/ranking?from={today}&to={today}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["report_type"] == "ranking"
    assert any(r[0] == "44" for r in data["rows"])  # row index 0 = route_code


@pytest.mark.asyncio
async def test_reports_get_empty_aggregates_renders_no_data(reports_client):
    """With no agg data seeded, the report renders gracefully (text + empty rows)."""
    client, agency_id, _ = reports_client
    resp = await client.get(f"/api/{agency_id}/reports/ranking")
    assert resp.status_code == 200
    data = resp.json()
    assert data["report_type"] == "ranking"
    assert data["rows"] == []
    assert isinstance(data["text"], str) and len(data["text"]) > 0


@pytest.mark.asyncio
async def test_reports_unknown_agency_returns_404(reports_client):
    client, _, _ = reports_client
    resp = await client.get("/api/99999/reports")
    assert resp.status_code == 404
