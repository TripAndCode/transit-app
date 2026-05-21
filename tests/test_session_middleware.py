"""Tests for ``SessionMiddleware``: anonymous, valid cookie, expired cookie, suspended user."""

import os
from datetime import datetime, timedelta, timezone

import asyncpg
import httpx
import pytest
from httpx import ASGITransport

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://localhost/transit")


async def _make_session(conn, *, role="user", suspended=False, expires_in=timedelta(days=30)):
    uid = (
        await conn.fetchrow(
            "INSERT INTO users (email, role, suspended_at) VALUES ($1, $2, $3) RETURNING user_id",
            f"u{datetime.now().timestamp()}@x",
            role,
            datetime.now(timezone.utc) if suspended else None,
        )
    )["user_id"]
    sid = "test-sid-" + str(uid)
    await conn.execute(
        "INSERT INTO sessions (sid, user_id, expires_at) VALUES ($1, $2, $3)",
        sid,
        uid,
        datetime.now(timezone.utc) + expires_in,
    )
    return sid, uid


@pytest.fixture
async def client(apply_schema):
    from api.main import app

    pool = await asyncpg.create_pool(DATABASE_URL)
    app.state.pool = pool
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    await pool.close()


@pytest.mark.asyncio
async def test_no_cookie_anonymous(client, aconn):
    resp = await client.get("/api/me")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_valid_cookie_loads_user(client, aconn):
    sid, uid = await _make_session(aconn)
    resp = await client.get("/api/me", cookies={"sid": sid})
    assert resp.status_code == 200
    assert resp.json()["user_id"] == uid


@pytest.mark.asyncio
async def test_expired_cookie_cleared(client, aconn):
    sid, _ = await _make_session(aconn, expires_in=timedelta(seconds=-1))
    resp = await client.get("/api/me", cookies={"sid": sid})
    assert resp.status_code == 401
    # cookie cleared on response
    set_cookie = resp.headers.get("set-cookie", "")
    assert "sid=" in set_cookie and "Max-Age=0" in set_cookie


@pytest.mark.asyncio
async def test_suspended_user_blocked(client, aconn):
    sid, _ = await _make_session(aconn, suspended=True)
    resp = await client.get("/api/me", cookies={"sid": sid})
    assert resp.status_code == 401
