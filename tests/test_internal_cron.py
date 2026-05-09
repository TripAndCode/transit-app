"""Internal cron endpoint — secret enforcement and 202-fast-return."""

import os
from unittest.mock import patch

import asyncpg
import httpx
import pytest
from httpx import ASGITransport

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://localhost/transit")


@pytest.fixture
async def cron_app(apply_schema):
    from api.main import app

    pool = await asyncpg.create_pool(DATABASE_URL)
    app.state.pool = pool
    yield app
    await pool.close()


@pytest.mark.anyio
async def test_cron_ingest_rejects_missing_header(cron_app, monkeypatch):
    monkeypatch.setenv("CRON_SECRET", "shh")
    transport = ASGITransport(app=cron_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.post("/internal/cron/ingest")
    assert r.status_code == 401


@pytest.mark.anyio
async def test_cron_ingest_rejects_wrong_secret(cron_app, monkeypatch):
    monkeypatch.setenv("CRON_SECRET", "shh")
    transport = ASGITransport(app=cron_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.post("/internal/cron/ingest", headers={"X-Cron-Secret": "nope"})
    assert r.status_code == 401


@pytest.mark.anyio
async def test_cron_ingest_503_when_secret_unset(cron_app, monkeypatch):
    monkeypatch.delenv("CRON_SECRET", raising=False)
    transport = ASGITransport(app=cron_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.post("/internal/cron/ingest", headers={"X-Cron-Secret": "anything"})
    assert r.status_code == 503


@pytest.mark.anyio
async def test_cron_ingest_returns_202_and_schedules_work(cron_app, monkeypatch):
    monkeypatch.setenv("CRON_SECRET", "shh")
    transport = ASGITransport(app=cron_app)
    # The background task spawns subprocess work we don't want during tests —
    # patch it to a no-op and just verify the dispatch.
    with patch("api.routers.internal._run_ingest_and_analyze") as fake:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.post("/internal/cron/ingest", headers={"X-Cron-Secret": "shh"})
        assert r.status_code == 202
        assert r.json() == {"status": "started"}
        # BackgroundTasks runs after the response is returned by the AsyncClient
        # client context exits, so by the time we're here the task has executed.
        fake.assert_called_once()
