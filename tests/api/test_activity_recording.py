"""Authenticated API requests are counted per user, route template and agency."""

from datetime import datetime, timedelta, timezone

import pytest

from api.activity import BUFFER
from api.security import token_hash


async def _session(conn):
    uid = await conn.fetchval(
        "INSERT INTO users (email) VALUES ($1) RETURNING user_id", f"rec-{datetime.now().timestamp()}@x"
    )
    sid = f"sid-rec-{uid}"
    await conn.execute(
        "INSERT INTO sessions (sid_hash, user_id, expires_at) VALUES ($1, $2, $3)",
        token_hash(sid),
        uid,
        datetime.now(timezone.utc) + timedelta(days=1),
    )
    return sid, uid


def _counts_for(uid):
    return {(k.route, k.method, k.agency_id, k.via_api_key): v for k, v in BUFFER.drain().items() if k.user_id == uid}


@pytest.mark.asyncio
async def test_a_signed_in_request_is_counted_by_route_template_and_agency(client, aconn):
    BUFFER.drain()
    sid, uid = await _session(aconn)
    await client.get("/api/7/overview/summary", cookies={"sid": sid})
    counts = _counts_for(uid)
    assert list(counts) == [("/api/{agency_id}/overview/summary", "GET", 7, False)]
    assert counts[("/api/{agency_id}/overview/summary", "GET", 7, False)][0] == 1


@pytest.mark.asyncio
async def test_an_api_key_request_is_counted_against_the_key_owner(client, aconn):
    BUFFER.drain()
    _, uid = await _session(aconn)
    raw = f"key-rec-{uid}"
    await aconn.execute(
        "INSERT INTO api_keys (key_hash, owner_email, tier, owner_user_id) VALUES ($1, 'o@x', 'pro', $2)",
        token_hash(raw),
        uid,
    )
    await client.get("/api/agencies", headers={"X-API-Key": raw})
    assert ("/api/agencies", "GET", None, True) in _counts_for(uid)


@pytest.mark.asyncio
async def test_signed_out_public_and_unmatched_requests_are_not_counted(client, aconn):
    BUFFER.drain()
    sid, _ = await _session(aconn)
    await client.get("/api/agencies")
    await client.get("/api/config", cookies={"sid": sid})
    await client.get("/api/no-such-route", cookies={"sid": sid})
    assert BUFFER.drain() == {}
