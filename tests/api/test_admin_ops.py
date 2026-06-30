"""Tests for GET /api/admin/ops — admin health dashboard endpoint."""

import os
from datetime import datetime, timedelta, timezone

import asyncpg
import httpx
import pytest
from httpx import ASGITransport

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://localhost/transit")


async def _seed_admin_session(conn) -> str:
    uid = (
        await conn.fetchrow(
            "INSERT INTO users (email, role) VALUES ($1, 'admin') RETURNING user_id",
            f"opsadmin{datetime.now().timestamp()}@x",
        )
    )["user_id"]
    sid = f"sid-ops-{uid}"
    await conn.execute(
        "INSERT INTO sessions (sid, user_id, expires_at) VALUES ($1, $2, $3)",
        sid, uid, datetime.now(timezone.utc) + timedelta(days=1),
    )
    return sid


@pytest.fixture
async def ops_client(apply_schema):
    from api.main import app

    pool = await asyncpg.create_pool(DATABASE_URL)
    app.state.pool = pool
    async with pool.acquire() as conn:
        await conn.execute(
            "TRUNCATE agencies, sessions, users, agg_meta, agg_feed_health, agg_route_daily CASCADE"
        )
        admin_sid = await _seed_admin_session(conn)
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c, admin_sid, pool
    await pool.close()


@pytest.mark.asyncio
async def test_ops_requires_admin(ops_client):
    c, _, _ = ops_client
    resp = await c.get("/api/admin/ops")
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_ops_returns_shape(ops_client):
    c, sid, _ = ops_client
    resp = await c.get("/api/admin/ops", cookies={"sid": sid})
    assert resp.status_code == 200
    body = resp.json()
    assert "migrations" in body
    assert "agencies" in body
    assert isinstance(body["agencies"], list)


@pytest.mark.asyncio
async def test_ops_migrations_up_to_date(ops_client):
    """After apply_schema, migrations.behind should be 0."""
    c, sid, _ = ops_client
    resp = await c.get("/api/admin/ops", cookies={"sid": sid})
    body = resp.json()
    assert body["migrations"] is not None
    assert body["migrations"]["behind"] == 0


@pytest.mark.asyncio
async def test_ops_agency_freshness(ops_client):
    """Agency with agg_meta row appears in agencies list with last_analyzed_at."""
    c, sid, pool = ops_client
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO agencies (agency_name, feed_url) VALUES ($1, $2) RETURNING agency_id",
            "OpsTestAgency", "http://ops.example.com",
        )
        aid = row["agency_id"]
        analyzed = datetime.now(timezone.utc) - timedelta(hours=3)
        await conn.execute(
            "INSERT INTO agg_meta (agency_id, analyzed_at) VALUES ($1, $2)",
            aid, analyzed,
        )
    resp = await c.get("/api/admin/ops", cookies={"sid": sid})
    body = resp.json()
    agency_row = next((a for a in body["agencies"] if a["agency_id"] == aid), None)
    assert agency_row is not None
    assert agency_row["last_analyzed_at"] is not None
    # ~3 hours ago, so analyze_age_hours should be roughly 3 (within 0.1 tolerance)
    assert agency_row["analyze_age_hours"] is not None
    assert 2.5 < agency_row["analyze_age_hours"] < 3.5


@pytest.mark.asyncio
async def test_ops_graceful_degradation(ops_client, monkeypatch):
    """A failing sub-check returns null for that section, not a 500."""
    from pipeline import health as health_mod

    async def boom(conn):
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(health_mod, "migration_status", boom)
    c, sid, _ = ops_client
    resp = await c.get("/api/admin/ops", cookies={"sid": sid})
    assert resp.status_code == 200
    assert resp.json()["migrations"] is None
