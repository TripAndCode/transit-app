"""Tests for ``GET``/``PUT``/``DELETE /api/me/llm-key`` (BYOK LLM credentials)."""

import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("LLM_KEY_ENCRYPTION_KEY", "zJj1v3nq7v3rj0aWq2p8m9s4b6d5f7h9k1n3q5s7u9w=")

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
async def test_get_llm_key_anonymous_401(me_client):
    resp = await me_client.get("/api/me/llm-key")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_get_llm_key_defaults_to_not_configured(me_client, aconn):
    sid, _uid = await _seed_user_and_session(aconn)
    resp = await me_client.get("/api/me/llm-key", cookies={"sid": sid})
    assert resp.status_code == 200
    assert resp.json() == {"configured": False, "provider": None, "key_suffix": None}


@pytest.mark.asyncio
async def test_put_llm_key_rejects_invalid_key_before_persisting(monkeypatch, me_client, aconn):
    async def fake_validate(provider, key):
        return False

    monkeypatch.setattr("api.routers.me.validate_provider_key", fake_validate)
    sid, _uid = await _seed_user_and_session(aconn)
    resp = await me_client.put(
        "/api/me/llm-key",
        json={"provider": "groq", "api_key": "bad"},
        cookies={"sid": sid},
        headers={"Origin": "http://test"},
    )
    assert resp.status_code == 400

    get_resp = await me_client.get("/api/me/llm-key", cookies={"sid": sid})
    assert get_resp.json()["configured"] is False


@pytest.mark.asyncio
async def test_put_llm_key_rejects_unsupported_provider(monkeypatch, me_client, aconn):
    called = False

    async def fake_validate(provider, key):
        nonlocal called
        called = True
        return True

    monkeypatch.setattr("api.routers.me.validate_provider_key", fake_validate)
    sid, _uid = await _seed_user_and_session(aconn)
    resp = await me_client.put(
        "/api/me/llm-key",
        json={"provider": "not_a_real_provider", "api_key": "whatever"},
        cookies={"sid": sid},
        headers={"Origin": "http://test"},
    )
    assert resp.status_code == 400
    # Validation (and persistence) never runs for an unsupported provider.
    assert called is False


@pytest.mark.asyncio
async def test_put_then_get_llm_key_never_returns_full_key(monkeypatch, me_client, aconn):
    async def fake_validate(provider, key):
        return True

    monkeypatch.setattr("api.routers.me.validate_provider_key", fake_validate)
    sid, _uid = await _seed_user_and_session(aconn)
    put_resp = await me_client.put(
        "/api/me/llm-key",
        json={"provider": "groq", "api_key": "gsk_realkey1234"},
        cookies={"sid": sid},
        headers={"Origin": "http://test"},
    )
    assert put_resp.status_code == 200
    assert "gsk_realkey1234" not in put_resp.text
    body = put_resp.json()
    assert body == {"configured": True, "provider": "groq", "key_suffix": "1234"}

    get_resp = await me_client.get("/api/me/llm-key", cookies={"sid": sid})
    body = get_resp.json()
    assert body["configured"] is True
    assert body["provider"] == "groq"
    assert body["key_suffix"] == "1234"
    assert "gsk_realkey1234" not in get_resp.text


@pytest.mark.asyncio
async def test_delete_llm_key_clears_configured_status(monkeypatch, me_client, aconn):
    async def fake_validate(provider, key):
        return True

    monkeypatch.setattr("api.routers.me.validate_provider_key", fake_validate)
    sid, _uid = await _seed_user_and_session(aconn)
    await me_client.put(
        "/api/me/llm-key",
        json={"provider": "groq", "api_key": "gsk_realkey1234"},
        cookies={"sid": sid},
        headers={"Origin": "http://test"},
    )
    resp = await me_client.delete(
        "/api/me/llm-key",
        cookies={"sid": sid},
        headers={"Origin": "http://test"},
    )
    assert resp.status_code == 204
    get_resp = await me_client.get("/api/me/llm-key", cookies={"sid": sid})
    assert get_resp.json()["configured"] is False


@pytest.mark.asyncio
async def test_put_llm_key_requires_same_origin(me_client, aconn):
    """Mutating the BYOK key without a same-origin Origin/Referer is rejected (CSRF guard)."""
    sid, _uid = await _seed_user_and_session(aconn)
    resp = await me_client.put(
        "/api/me/llm-key",
        json={"provider": "groq", "api_key": "gsk_realkey1234"},
        cookies={"sid": sid},
        headers={"Origin": "http://evil.example"},
    )
    assert resp.status_code == 403
