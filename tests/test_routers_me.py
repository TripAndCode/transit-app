"""Tests for self-service ``/api/me`` endpoints: profile, sessions, presets."""

import os
from datetime import datetime, timedelta, timezone

import asyncpg
import httpx
import pytest
from httpx import ASGITransport

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://localhost/transit")


async def _seed_user_and_session(conn, *, role="user"):
    uid = (
        await conn.fetchrow(
            "INSERT INTO users (email, name, role) VALUES ($1, $2, $3) RETURNING user_id",
            f"u{datetime.now().timestamp()}@x",
            "Yo",
            role,
        )
    )["user_id"]
    sid = f"sid-{uid:0>30}"
    await conn.execute(
        "INSERT INTO sessions (sid, user_id, expires_at, user_agent) VALUES ($1, $2, $3, $4)",
        sid,
        uid,
        datetime.now(timezone.utc) + timedelta(days=30),
        "test-ua",
    )
    return sid, uid


@pytest.fixture
async def me_client(apply_schema):
    from api.main import app

    pool = await asyncpg.create_pool(DATABASE_URL)
    app.state.pool = pool
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    await pool.close()


@pytest.mark.asyncio
async def test_me_anonymous_401(me_client):
    resp = await me_client.get("/api/me")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_me_returns_profile(me_client, aconn):
    sid, uid = await _seed_user_and_session(aconn)
    resp = await me_client.get("/api/me", cookies={"sid": sid})
    assert resp.status_code == 200
    body = resp.json()
    assert body["user_id"] == uid
    assert body["name"] == "Yo"
    assert body["role"] == "user"
    assert body["identities"] == []


@pytest.mark.asyncio
async def test_preset_create_list_delete(me_client, aconn, aagency_id):
    """Filter presets: create, list, delete round-trip."""
    sid, _uid = await _seed_user_and_session(aconn)
    body = {"agency_id": aagency_id, "name": "朝ラッシュ", "range_ctx": {"time_band": "0700-1000"}}
    r = await me_client.post(
        "/api/me/presets",
        json=body,
        cookies={"sid": sid},
        headers={"Origin": "http://test"},
    )
    assert r.status_code == 201
    pid = r.json()["preset_id"]

    # collision
    r2 = await me_client.post(
        "/api/me/presets",
        json=body,
        cookies={"sid": sid},
        headers={"Origin": "http://test"},
    )
    assert r2.status_code == 409

    r3 = await me_client.get(f"/api/me/presets?agency_id={aagency_id}", cookies={"sid": sid})
    assert r3.status_code == 200
    assert len(r3.json()) == 1

    r4 = await me_client.delete(
        f"/api/me/presets/{pid}",
        cookies={"sid": sid},
        headers={"Origin": "http://test"},
    )
    assert r4.status_code == 204


@pytest.mark.asyncio
async def test_other_user_cannot_delete_preset(me_client, aconn, aagency_id):
    sid_a, _ = await _seed_user_and_session(aconn)
    sid_b, _ = await _seed_user_and_session(aconn)
    r = await me_client.post(
        "/api/me/presets",
        json={"agency_id": aagency_id, "name": "x", "range_ctx": {}},
        cookies={"sid": sid_a},
        headers={"Origin": "http://test"},
    )
    pid = r.json()["preset_id"]
    r2 = await me_client.delete(
        f"/api/me/presets/{pid}",
        cookies={"sid": sid_b},
        headers={"Origin": "http://test"},
    )
    assert r2.status_code == 404


@pytest.mark.asyncio
async def test_sessions_listed_then_revoked(me_client, aconn):
    """Sessions list shows the active session; revoking it logs out."""
    sid, _uid = await _seed_user_and_session(aconn)
    r = await me_client.get("/api/me/sessions", cookies={"sid": sid})
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    prefix = rows[0]["sid_prefix"]
    assert len(prefix) >= 12
    r2 = await me_client.delete(
        f"/api/me/sessions/{prefix}",
        cookies={"sid": sid},
        headers={"Origin": "http://test"},
    )
    assert r2.status_code == 204
