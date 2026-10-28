"""Tests for self-service ``/api/me`` endpoints: profile, sessions, presets."""

from datetime import timedelta

import httpx
import pytest
from httpx import ASGITransport

from api.security import token_hash
from tests.conftest import TEST_ORIGIN, _test_pool
from tests.fixtures.users import add_session, add_session_digest, seed_session


async def _seed_user_and_session(conn, *, role="user"):
    sid, uid, _ = await seed_session(conn, role=role, name="Yo", user_agent="test-ua")
    return sid, uid


@pytest.fixture
async def me_client(apply_schema):
    from api.main import app

    pool = await _test_pool()
    app.state.pool = pool
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    await pool.close()


@pytest.mark.asyncio
async def test_me_anonymous_is_null(me_client):
    """Signed out is a normal answer to who-am-I, not a refusal: the SPA asks it on every page."""
    resp = await me_client.get("/api/me")
    assert resp.status_code == 200
    assert resp.json() is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/me/sessions"),
        ("GET", "/api/me/presets"),
        ("GET", "/api/me/llm-key"),
        ("GET", "/api/me/export"),
        ("DELETE", "/api/me"),
    ],
)
async def test_signed_in_only_me_routes_still_refuse_anonymous(me_client, method, path):
    resp = await me_client.request(method, path, json={"confirm_email": "x@x"}, headers={"Origin": TEST_ORIGIN})
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
async def test_sessions_mark_the_one_making_the_request(me_client, aconn):
    sid, uid = await _seed_user_and_session(aconn)
    other = await add_session(aconn, uid, user_agent="other-ua")
    rows = (await me_client.get("/api/me/sessions", cookies={"sid": sid})).json()
    current = {row["sid_prefix"]: row["current"] for row in rows}
    assert current == {token_hash(sid)[:12]: True, token_hash(other)[:12]: False}


@pytest.mark.asyncio
async def test_sessions_list_omits_expired_sessions(me_client, aconn):
    sid, uid = await _seed_user_and_session(aconn)
    await add_session(aconn, uid, expires_in=timedelta(minutes=-1), user_agent="dead-ua")
    rows = (await me_client.get("/api/me/sessions", cookies={"sid": sid})).json()
    assert [row["sid_prefix"] for row in rows] == [token_hash(sid)[:12]]


@pytest.mark.asyncio
async def test_sessions_listed_then_revoked(me_client, aconn):
    """Sessions list shows the active session; revoking it logs out."""
    sid, _uid = await _seed_user_and_session(aconn)
    r = await me_client.get("/api/me/sessions", cookies={"sid": sid})
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    prefix = rows[0]["sid_prefix"]
    # The handle is a prefix of the stored digest, never of the session id.
    assert prefix == token_hash(sid)[:12]
    r2 = await me_client.delete(
        f"/api/me/sessions/{prefix}",
        cookies={"sid": sid},
        headers={"Origin": "http://test"},
    )
    assert r2.status_code == 204


# A shared display-length prefix with letters in it, so uppercasing it changes it.
_PREFIX = "abcdef012345"


async def _hashes(conn, uid):
    return set(await conn.fetchval("SELECT array_agg(sid_hash) FROM sessions WHERE user_id=$1", uid) or [])


async def _revoke(me_client, sid, prefix):
    return await me_client.delete(f"/api/me/sessions/{prefix}", cookies={"sid": sid}, headers={"Origin": "http://test"})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("prefix", "detail"),
    [
        (_PREFIX[:11], "prefix too short"),
        (_PREFIX[:11] + "g", "invalid prefix"),
        (_PREFIX[:11] + "_", "invalid prefix"),
        (_PREFIX.upper(), "invalid prefix"),
    ],
)
async def test_revoking_a_session_refuses_a_prefix_no_stored_hash_can_have(me_client, aconn, prefix, detail):
    """Uppercase is rejected, not folded: a stored hash is lowercase hex."""
    sid, uid = await _seed_user_and_session(aconn)
    await add_session_digest(aconn, uid, _PREFIX + "a" * 52)
    before = await _hashes(aconn, uid)
    r = await _revoke(me_client, sid, prefix)
    assert (r.status_code, r.json()["detail"]) == (400, detail)
    assert await _hashes(aconn, uid) == before


@pytest.mark.asyncio
async def test_revoking_a_session_404s_when_no_session_matches(me_client, aconn):
    sid, uid = await _seed_user_and_session(aconn)
    await add_session_digest(aconn, uid, _PREFIX + "a" * 52)
    before = await _hashes(aconn, uid)
    r = await _revoke(me_client, sid, "fedcba987654")
    assert (r.status_code, r.json()["detail"]) == (404, "session not found")
    assert await _hashes(aconn, uid) == before


@pytest.mark.asyncio
async def test_revoking_a_session_by_a_unique_prefix_removes_only_that_session(me_client, aconn):
    sid, uid = await _seed_user_and_session(aconn)
    await add_session_digest(aconn, uid, _PREFIX + "a" * 52)
    r = await _revoke(me_client, sid, _PREFIX)
    assert r.status_code == 204
    assert await _hashes(aconn, uid) == {token_hash(sid)}


@pytest.mark.asyncio
async def test_revoking_a_session_409s_on_an_ambiguous_prefix(me_client, aconn):
    sid, uid = await _seed_user_and_session(aconn)
    await add_session_digest(aconn, uid, _PREFIX + "a" * 52)
    await add_session_digest(aconn, uid, _PREFIX + "c" * 52)
    before = await _hashes(aconn, uid)
    r = await _revoke(me_client, sid, _PREFIX)
    assert (r.status_code, r.json()["detail"]) == (409, "prefix matches multiple sessions")
    assert await _hashes(aconn, uid) == before


@pytest.mark.asyncio
async def test_revoking_a_session_never_reaches_another_users_session(me_client, aconn):
    sid, _uid = await _seed_user_and_session(aconn)
    _other_sid, other_uid = await _seed_user_and_session(aconn)
    await add_session_digest(aconn, other_uid, _PREFIX + "b" * 52)
    before = await _hashes(aconn, other_uid)
    r = await _revoke(me_client, sid, _PREFIX)
    assert (r.status_code, r.json()["detail"]) == (404, "session not found")
    assert await _hashes(aconn, other_uid) == before


@pytest.mark.asyncio
async def test_revoking_a_session_counts_matches_among_the_callers_sessions_only(me_client, aconn):
    """Another user holding a session with the same prefix makes it neither
    ambiguous nor theirs to lose."""
    sid, uid = await _seed_user_and_session(aconn)
    _other_sid, other_uid = await _seed_user_and_session(aconn)
    await add_session_digest(aconn, uid, _PREFIX + "a" * 52)
    await add_session_digest(aconn, other_uid, _PREFIX + "b" * 52)
    other_before = await _hashes(aconn, other_uid)
    r = await _revoke(me_client, sid, _PREFIX)
    assert r.status_code == 204
    assert await _hashes(aconn, uid) == {token_hash(sid)}
    assert await _hashes(aconn, other_uid) == other_before
