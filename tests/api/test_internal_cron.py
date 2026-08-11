"""Internal cron endpoint — secret enforcement and 202-fast-return."""

import os
from unittest.mock import MagicMock, patch

import asyncpg
import httpx
import psycopg2
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


@pytest.fixture
def two_agencies(apply_schema):
    """One active, one soft-deleted agency. Cleaned up after the test."""
    conn = psycopg2.connect(DATABASE_URL)
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agencies (agency_name, feed_url) VALUES (%s, %s) RETURNING agency_id",
            ("Cron Active", "http://cron-active.example.com"),
        )
        active_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO agencies (agency_name, feed_url, deleted_at) VALUES (%s, %s, now()) RETURNING agency_id",
            ("Cron Deleted", "http://cron-deleted.example.com"),
        )
        deleted_id = cur.fetchone()[0]
    conn.commit()
    try:
        yield active_id, deleted_id
    finally:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM agencies WHERE agency_id IN (%s, %s)", (active_id, deleted_id))
        conn.commit()
        conn.close()


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


def test_run_ingest_and_analyze_skips_deleted_agency(two_agencies, monkeypatch):
    """The actual cron work loop must not ingest/analyze a soft-deleted agency."""
    active_id, deleted_id = two_agencies
    monkeypatch.setenv("DATABASE_URL", DATABASE_URL)

    from api.routers.internal import _run_ingest_and_analyze

    with (
        patch("pipeline.ingest.ingest_live") as fake_ingest,
        patch("pipeline.analyze.analyze") as fake_analyze,
        patch("pipeline.freshness.check_agg_freshness", return_value=[]),
        patch("pipeline.clickhouse.get_client", return_value=MagicMock()),
    ):
        _run_ingest_and_analyze()

    ingested_ids = [c.args[0] for c in fake_ingest.call_args_list]
    analyzed_ids = [c.args[0] for c in fake_analyze.call_args_list]
    assert active_id in ingested_ids
    assert deleted_id not in ingested_ids
    assert active_id in analyzed_ids
    assert deleted_id not in analyzed_ids
