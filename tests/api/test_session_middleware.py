"""Tests for ``SessionMiddleware``: anonymous, valid cookie, expired cookie, suspended user."""

from datetime import timedelta

import pytest

from tests.fixtures.users import seed_session


@pytest.mark.asyncio
async def test_no_cookie_anonymous(client, aconn):
    resp = await client.get("/api/me")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_valid_cookie_loads_user(client, aconn):
    sid, uid, _ = await seed_session(aconn)
    resp = await client.get("/api/me", cookies={"sid": sid})
    assert resp.status_code == 200
    assert resp.json()["user_id"] == uid


@pytest.mark.asyncio
async def test_expired_cookie_cleared(client, aconn):
    sid = (await seed_session(aconn, expires_in=timedelta(seconds=-1))).sid
    resp = await client.get("/api/me", cookies={"sid": sid})
    assert resp.status_code == 401
    # cookie cleared on response
    set_cookie = resp.headers.get("set-cookie", "")
    assert "sid=" in set_cookie and "Max-Age=0" in set_cookie


@pytest.mark.asyncio
async def test_suspended_user_blocked(client, aconn):
    sid = (await seed_session(aconn, suspended=True)).sid
    resp = await client.get("/api/me", cookies={"sid": sid})
    assert resp.status_code == 401
