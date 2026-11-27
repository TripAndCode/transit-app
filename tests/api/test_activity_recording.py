"""API requests are counted per route template and agency: a signed-in
caller into ``user_activity_daily``, everyone else into
``anonymous_activity_daily``."""

import pytest

from api.activity import ANONYMOUS_BUFFER, BUFFER
from tests.fixtures.users import seed_session


def _counts_for(uid):
    return {(k.route, k.method, k.agency_id): v for k, v in BUFFER.drain().items() if k.user_id == uid}


def _anonymous_counts():
    return {(k.route, k.method, k.agency_id): v for k, v in ANONYMOUS_BUFFER.drain().items()}


@pytest.mark.asyncio
async def test_a_signed_in_request_is_counted_by_route_template_and_agency(client, aconn):
    BUFFER.drain()
    sid, uid, _ = await seed_session(aconn)
    await client.get("/api/7/overview/summary", cookies={"sid": sid})
    counts = _counts_for(uid)
    assert list(counts) == [("/api/{agency_id}/overview/summary", "GET", 7)]
    assert counts[("/api/{agency_id}/overview/summary", "GET", 7)][0] == 1


@pytest.mark.asyncio
async def test_a_signed_out_request_lands_in_the_anonymous_buffer_not_the_user_one(client, aconn):
    BUFFER.drain()
    ANONYMOUS_BUFFER.drain()
    await client.get("/api/agencies")
    assert _anonymous_counts() == {("/api/agencies", "GET", None): [1, 0]}
    assert BUFFER.drain() == {}


@pytest.mark.asyncio
async def test_a_request_carrying_an_arbitrary_api_key_header_is_served_and_counted_anonymously(client, aconn):
    """No API-key credential exists any more: the header rides along like any
    other, buys no tier, and is served -- a 200, not a 401 -- and counted the
    same as a signed-out request."""
    BUFFER.drain()
    ANONYMOUS_BUFFER.drain()
    r = await client.get("/api/agencies", headers={"X-API-Key": "whatever-arbitrary-value"})
    assert r.status_code == 200
    assert _anonymous_counts() == {("/api/agencies", "GET", None): [1, 0]}
    assert BUFFER.drain() == {}


@pytest.mark.asyncio
async def test_a_signed_out_request_keeps_its_agency_only_when_it_succeeded(client, aagency_id):
    ANONYMOUS_BUFFER.drain()
    assert (await client.get(f"/api/agencies/{aagency_id}")).status_code == 200
    assert (await client.get("/api/agencies/987654")).status_code == 404
    assert (await client.get("/api/987654/overview/summary")).status_code == 404
    assert _anonymous_counts() == {
        ("/api/agencies/{agency_id}", "GET", aagency_id): [1, 0],
        ("/api/agencies/{agency_id}", "GET", None): [1, 1],
        ("/api/{agency_id}/overview/summary", "GET", None): [1, 1],
    }


@pytest.mark.asyncio
async def test_sign_in_plumbing_and_unmatched_requests_are_not_counted(client, aconn):
    BUFFER.drain()
    ANONYMOUS_BUFFER.drain()
    sid = (await seed_session(aconn)).sid
    await client.get("/api/config", cookies={"sid": sid})
    await client.get("/api/me", cookies={"sid": sid})
    await client.post("/api/auth/logout")
    await client.get("/api/no-such-route", cookies={"sid": sid})
    assert BUFFER.drain() == {}
    assert ANONYMOUS_BUFFER.drain() == {}


@pytest.mark.asyncio
async def test_a_method_the_route_does_not_serve_is_not_counted(client, aconn):
    BUFFER.drain()
    ANONYMOUS_BUFFER.drain()
    sid = (await seed_session(aconn)).sid
    assert (await client.request("FOOBAR", "/api/agencies")).status_code == 405
    assert (await client.request("PUT", "/api/agencies", cookies={"sid": sid})).status_code == 405
    assert BUFFER.drain() == {}
    assert ANONYMOUS_BUFFER.drain() == {}


@pytest.mark.asyncio
async def test_a_signed_out_snapshot_read_is_counted(client, aconn):
    ANONYMOUS_BUFFER.drain()
    await client.get("/api/snapshots/abcdefghijklmnopqrstuv")
    counts = _anonymous_counts()
    assert ("/api/snapshots/{snapshot_id}", "GET", None) in counts


@pytest.mark.asyncio
async def test_a_signed_out_read_is_counted_while_sso_is_configured(client, sso_on):
    BUFFER.drain()
    ANONYMOUS_BUFFER.drain()
    r = await client.get("/api/agencies")
    assert r.status_code == 200
    assert _anonymous_counts() == {("/api/agencies", "GET", None): [1, 0]}
    assert BUFFER.drain() == {}


@pytest.mark.asyncio
async def test_a_signed_out_401_from_an_admin_route_is_counted_as_an_error(client, sso_on):
    BUFFER.drain()
    ANONYMOUS_BUFFER.drain()
    r = await client.get("/api/admin/flags")
    assert r.status_code == 401
    assert _anonymous_counts() == {("/api/admin/flags", "GET", None): [1, 1]}
    assert BUFFER.drain() == {}
