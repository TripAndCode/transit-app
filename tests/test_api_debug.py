"""Tests for the env-gated perf debug endpoints.

Contract:
1. GET  /api/debug/perf  -> 200; body has ops, caches, pool {size, idle}.
2. POST /api/debug/perf/reset -> 200; subsequent GET shows ops == {}.
3. PERF_DEBUG_ENABLED=false -> 404 on both endpoints.
"""

import os

import asyncpg
import httpx
import pytest
from httpx import ASGITransport

from pipeline import perf


@pytest.fixture(autouse=True)
def reset_perf():
    """Ensure a clean perf registry before and after every test."""
    perf.reset()
    yield
    perf.reset()


@pytest.fixture
async def debug_client(apply_schema):
    from api.main import app

    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"])
    app.state.pool = pool

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
    await pool.close()


@pytest.mark.asyncio
async def test_perf_snapshot(debug_client):
    """GET /api/debug/perf returns 200 with ops, caches, and pool keys."""
    # Record a label so ops is non-empty and verifiable.
    perf.record("test.label", 42.0)

    resp = await debug_client.get("/api/debug/perf")
    assert resp.status_code == 200, resp.text

    body = resp.json()
    assert "ops" in body
    assert "caches" in body
    assert "pool" in body

    assert "test.label" in body["ops"]
    pool_info = body["pool"]
    assert "size" in pool_info
    assert "idle" in pool_info


@pytest.mark.asyncio
async def test_perf_reset(debug_client):
    """POST /api/debug/perf/reset clears the registry; subsequent GET shows ops == {}."""
    perf.record("pre.reset", 10.0)

    reset_resp = await debug_client.post("/api/debug/perf/reset")
    assert reset_resp.status_code == 200
    assert reset_resp.json() == {"status": "reset"}

    snap_resp = await debug_client.get("/api/debug/perf")
    assert snap_resp.status_code == 200
    body = snap_resp.json()
    assert body["ops"] == {}


@pytest.mark.asyncio
async def test_perf_disabled(monkeypatch, debug_client):
    """Both endpoints return 404 when PERF_DEBUG_ENABLED=false."""
    monkeypatch.setenv("PERF_DEBUG_ENABLED", "false")

    get_resp = await debug_client.get("/api/debug/perf")
    assert get_resp.status_code == 404

    post_resp = await debug_client.post("/api/debug/perf/reset")
    assert post_resp.status_code == 404
