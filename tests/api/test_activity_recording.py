"""Authenticated API requests are counted per user, route template and agency."""

import pytest

from api.activity import BUFFER
from api.security import token_hash
from tests.fixtures.users import seed_session


def _counts_for(uid):
    return {(k.route, k.method, k.agency_id, k.via_api_key): v for k, v in BUFFER.drain().items() if k.user_id == uid}


@pytest.mark.asyncio
async def test_a_signed_in_request_is_counted_by_route_template_and_agency(client, aconn):
    BUFFER.drain()
    sid, uid, _ = await seed_session(aconn)
    await client.get("/api/7/overview/summary", cookies={"sid": sid})
    counts = _counts_for(uid)
    assert list(counts) == [("/api/{agency_id}/overview/summary", "GET", 7, False)]
    assert counts[("/api/{agency_id}/overview/summary", "GET", 7, False)][0] == 1


@pytest.mark.asyncio
async def test_an_api_key_request_is_counted_against_the_key_owner(client, aconn):
    BUFFER.drain()
    uid = (await seed_session(aconn)).user_id
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
    sid = (await seed_session(aconn)).sid
    await client.get("/api/agencies")
    await client.get("/api/config", cookies={"sid": sid})
    await client.get("/api/no-such-route", cookies={"sid": sid})
    assert BUFFER.drain() == {}
