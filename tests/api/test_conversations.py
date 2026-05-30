"""HTTP-layer tests for /api/{agency_id}/conversations/*."""
from __future__ import annotations

import os

import asyncpg
import httpx
import pytest
from httpx import ASGITransport

from tests.conftest import TEST_ORIGIN

DATABASE_URL = os.environ["DATABASE_URL"]
_CSRF = {"Origin": TEST_ORIGIN}


@pytest.fixture
async def conv_app(apply_schema):
    """Agency + a user; client authed as that user."""
    from api.main import app

    pool = await asyncpg.create_pool(DATABASE_URL)
    app.state.pool = pool

    async with pool.acquire() as c:
        await c.execute("DELETE FROM ask_conversations")
        a = await c.fetchrow(
            "INSERT INTO agencies (agency_name, feed_url) VALUES ('T', 'http://t') RETURNING agency_id"
        )
        u = await c.fetchrow(
            "INSERT INTO users (email, name, role) VALUES ('t@test', 'T', 'user') RETURNING user_id"
        )
    yield app, a["agency_id"], u["user_id"], pool
    async with pool.acquire() as c:
        await c.execute("DELETE FROM ask_conversations")
        await c.execute("DELETE FROM users WHERE email IN ('t@test', 'other@test')")
        await c.execute("TRUNCATE agencies CASCADE")
    await pool.close()


def _authed_client(app, user_id: int):
    """Build an httpx async client with an override that injects the user."""
    from api.deps import get_current_user
    from api.security import User

    fake_user = User(
        user_id=user_id,
        email="t@test",
        name="T",
        avatar_url=None,
        role="user",
        suspended_at=None,
    )
    app.dependency_overrides[get_current_user] = lambda: fake_user
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_create_and_list_conversation(conv_app):
    app, agency, uid, _ = conv_app
    async with _authed_client(app, uid) as c:
        r = await c.post(f"/api/{agency}/conversations",
                         json={"title": "First", "filter_ctx": {"dow": "weekday"}},
                         headers=_CSRF)
        assert r.status_code == 200, r.text
        conv_id = r.json()["conversation_id"]
        r2 = await c.get(f"/api/{agency}/conversations")
        assert r2.status_code == 200
        assert any(cv["conversation_id"] == conv_id for cv in r2.json())


@pytest.mark.asyncio
async def test_get_others_conversation_is_404(conv_app):
    app, agency, uid, pool = conv_app
    # Create with uid; then create a second user and try to access.
    async with _authed_client(app, uid) as c:
        r = await c.post(f"/api/{agency}/conversations",
                         json={"title": "X", "filter_ctx": {}}, headers=_CSRF)
        conv_id = r.json()["conversation_id"]
    async with pool.acquire() as conn:
        other = await conn.fetchrow(
            "INSERT INTO users (email, name, role) VALUES ('other@test', 'O', 'user') RETURNING user_id"
        )
    async with _authed_client(app, other["user_id"]) as c:
        r = await c.get(f"/api/{agency}/conversations/{conv_id}")
        assert r.status_code == 404  # masquerade as not-found for non-owners


@pytest.mark.asyncio
async def test_patch_and_delete(conv_app):
    app, agency, uid, _ = conv_app
    async with _authed_client(app, uid) as c:
        r = await c.post(f"/api/{agency}/conversations",
                         json={"title": "X", "filter_ctx": {}}, headers=_CSRF)
        conv_id = r.json()["conversation_id"]
        r2 = await c.patch(f"/api/{agency}/conversations/{conv_id}",
                           json={"title": "Renamed", "pinned": True}, headers=_CSRF)
        assert r2.status_code == 200, r2.text
        assert r2.json()["title"] == "Renamed"
        assert r2.json()["pinned"] is True
        r3 = await c.delete(f"/api/{agency}/conversations/{conv_id}", headers=_CSRF)
        assert r3.status_code == 200


@pytest.mark.asyncio
async def test_append_message_for_chip(conv_app):
    """Tapping a known chip appends both a user message and an assistant message."""
    app, agency, uid, pool = conv_app
    # Seed a route so describe_data has something to dispatch.
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO static_routes (agency_id, route_id, route_short_name) "
            "VALUES ($1, 'R1', 'R1')", agency,
        )
    async with _authed_client(app, uid) as c:
        cr = await c.post(f"/api/{agency}/conversations",
                          json={"title": "X", "filter_ctx": {}}, headers=_CSRF)
        conv_id = cr.json()["conversation_id"]
        r = await c.post(f"/api/{agency}/conversations/{conv_id}/messages",
                         json={"chip_id": "meta-routes"}, headers=_CSRF)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["assistant"]["tool"] == "describe_data"
        assert body["assistant"]["signature_hash"] is not None
        # Conversation list shows both messages now
        ml = await c.get(f"/api/{agency}/conversations/{conv_id}/messages")
        assert ml.status_code == 200
        assert len(ml.json()) == 2  # user + assistant


@pytest.mark.asyncio
async def test_append_message_unknown_chip_400(conv_app):
    app, agency, uid, _ = conv_app
    async with _authed_client(app, uid) as c:
        cr = await c.post(f"/api/{agency}/conversations",
                          json={"title": "X", "filter_ctx": {}}, headers=_CSRF)
        conv_id = cr.json()["conversation_id"]
        r = await c.post(f"/api/{agency}/conversations/{conv_id}/messages",
                         json={"chip_id": "does-not-exist"}, headers=_CSRF)
        assert r.status_code == 400


@pytest.mark.asyncio
async def test_append_message_tool_args_path(conv_app):
    """Builder direct-dispatch path: {tool, args} without chip_id appends messages."""
    app, agency, uid, pool = conv_app
    # Seed a route so describe_data tool has something to work with.
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO static_routes (agency_id, route_id, route_short_name) "
            "VALUES ($1, 'R1', 'R1')", agency,
        )
    async with _authed_client(app, uid) as c:
        cr = await c.post(f"/api/{agency}/conversations",
                          json={"title": "Builder test", "filter_ctx": {}}, headers=_CSRF)
        conv_id = cr.json()["conversation_id"]
        r = await c.post(
            f"/api/{agency}/conversations/{conv_id}/messages",
            json={"tool": "describe_data", "args": {}},
            headers=_CSRF,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        # chip_id should be null in both messages for the builder path
        assert body["user"]["chip_id"] is None
        assert body["assistant"]["tool"] == "describe_data"
        # Two messages appended
        ml = await c.get(f"/api/{agency}/conversations/{conv_id}/messages")
        assert ml.status_code == 200
        assert len(ml.json()) == 2


@pytest.mark.asyncio
async def test_append_message_both_chip_and_tool_400(conv_app):
    """Providing both chip_id and tool+args is a 400."""
    app, agency, uid, _ = conv_app
    async with _authed_client(app, uid) as c:
        cr = await c.post(f"/api/{agency}/conversations",
                          json={"title": "X", "filter_ctx": {}}, headers=_CSRF)
        conv_id = cr.json()["conversation_id"]
        r = await c.post(
            f"/api/{agency}/conversations/{conv_id}/messages",
            json={"chip_id": "meta-routes", "tool": "describe_data", "args": {}},
            headers=_CSRF,
        )
        assert r.status_code == 400


@pytest.mark.asyncio
async def test_append_message_neither_chip_nor_tool_400(conv_app):
    """Providing neither chip_id nor tool+args is a 400."""
    app, agency, uid, _ = conv_app
    async with _authed_client(app, uid) as c:
        cr = await c.post(f"/api/{agency}/conversations",
                          json={"title": "X", "filter_ctx": {}}, headers=_CSRF)
        conv_id = cr.json()["conversation_id"]
        r = await c.post(
            f"/api/{agency}/conversations/{conv_id}/messages",
            json={},
            headers=_CSRF,
        )
        assert r.status_code == 400


@pytest.mark.asyncio
async def test_migrate_anon_idempotent(conv_app):
    app, agency, uid, _ = conv_app
    payload = {"threads": [
        {"client_id": "anon-1", "title": "Anon A", "filter_ctx": {}, "pinned": False,
         "created_at": "2026-05-29T10:00:00", "updated_at": "2026-05-29T10:00:00", "messages": []},
        {"client_id": "anon-2", "title": "Anon B", "filter_ctx": {"dow": "weekday"}, "pinned": True,
         "created_at": "2026-05-29T11:00:00", "updated_at": "2026-05-29T11:00:00", "messages": []},
    ]}
    async with _authed_client(app, uid) as c:
        r1 = await c.post(f"/api/{agency}/conversations/migrate-anon", json=payload, headers=_CSRF)
        assert r1.status_code == 200, r1.text
        assert r1.json()["inserted"] == 2
        r2 = await c.post(f"/api/{agency}/conversations/migrate-anon", json=payload, headers=_CSRF)
        assert r2.json()["inserted"] == 0
        r3 = await c.get(f"/api/{agency}/conversations")
        assert len(r3.json()) == 2
