"""The API refuses signed-out callers while sign-in is required."""

from datetime import timedelta

import pytest

from api.security import token_hash
from pipeline.flags import invalidate as invalidate_flags
from tests.conftest import TEST_ORIGIN
from tests.fixtures.users import seed_session

_SSO = {
    "SESSION_SIGNING_KEY": "test-signing-key",
    "GOOGLE_CLIENT_ID": "g",
    "GOOGLE_CLIENT_SECRET": "gs",
    "GITHUB_CLIENT_ID": "h",
    "GITHUB_CLIENT_SECRET": "hs",
}
_AUTH_REQUIRED = {"detail": "auth required"}


@pytest.fixture
def sso_on(monkeypatch):
    for key, value in _SSO.items():
        monkeypatch.setenv(key, value)
    invalidate_flags()


async def _api_key(conn, uid):
    raw = f"key-gate-{uid}"
    await conn.execute(
        "INSERT INTO api_keys (key_hash, owner_email, tier, owner_user_id) VALUES ($1, $2, 'pro', $3)",
        token_hash(raw),
        "owner@x",
        uid,
    )
    return raw


@pytest.mark.asyncio
async def test_signed_out_read_is_refused_while_sign_in_is_required(client, sso_on):
    r = await client.get("/api/agencies")
    assert r.status_code == 401
    assert r.json() == _AUTH_REQUIRED


@pytest.mark.asyncio
async def test_signed_in_read_passes(client, aconn, sso_on):
    sid = (await seed_session(aconn)).sid
    r = await client.get("/api/agencies", cookies={"sid": sid})
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_expired_session_is_refused(client, aconn, sso_on):
    sid = (await seed_session(aconn, expires_in=timedelta(seconds=-1))).sid
    r = await client.get("/api/agencies", cookies={"sid": sid})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_valid_api_key_passes(client, aconn, sso_on):
    uid = (await seed_session(aconn)).user_id
    raw = await _api_key(aconn, uid)
    r = await client.get("/api/agencies", headers={"X-API-Key": raw})
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_sign_in_flow_and_config_stay_reachable(client, sso_on):
    assert (await client.get("/api/config")).status_code == 200
    assert (await client.get("/health")).status_code == 200
    r = await client.post("/api/auth/logout", headers={"Origin": TEST_ORIGIN})
    assert r.status_code == 204


@pytest.mark.asyncio
async def test_without_sso_the_api_stays_open(client):
    r = await client.get("/api/agencies")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_the_kill_switch_reopens_the_api(client, sso_on, monkeypatch):
    monkeypatch.setenv("LOGIN_REQUIRED", "false")
    invalidate_flags()
    r = await client.get("/api/agencies")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_config_reports_whether_sign_in_is_required(client, sso_on):
    assert (await client.get("/api/config")).json()["login_required"] is True


@pytest.mark.asyncio
async def test_config_reports_sign_in_not_required_without_sso(client):
    assert (await client.get("/api/config")).json()["login_required"] is False
