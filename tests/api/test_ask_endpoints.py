"""Endpoint tests for /ask/suggest, /ask/build-schema, /ask/edit-action."""

from __future__ import annotations

import os

import asyncpg
import httpx
import pytest
from httpx import ASGITransport

from tests.conftest import TEST_ORIGIN

DATABASE_URL = os.environ["DATABASE_URL"]
_CSRF_HEADERS = {"Origin": TEST_ORIGIN}


@pytest.fixture
async def ask_endpoints_app(apply_schema):
    """Agency + a small seed so the endpoints have something to work with."""
    from api.main import app

    pool = await asyncpg.create_pool(DATABASE_URL)
    app.state.pool = pool

    async with pool.acquire() as c:
        await c.execute("DELETE FROM ask_intent_cache")
        await c.execute("DELETE FROM rag_chunks")
        row = await c.fetchrow(
            "INSERT INTO agencies (agency_name, feed_url) VALUES ('T', 'http://t') RETURNING agency_id"
        )
        agency_id = row["agency_id"]

    yield app, agency_id, pool

    async with pool.acquire() as c:
        await c.execute("DELETE FROM ask_intent_cache")
        await c.execute("DELETE FROM rag_chunks")
        await c.execute("DELETE FROM agencies WHERE agency_id=$1", agency_id)
    await pool.close()


@pytest.fixture
async def ask_endpoints_client(ask_endpoints_app):
    app, agency_id, pool = ask_endpoints_app
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, agency_id, pool


@pytest.mark.asyncio
async def test_build_schema_returns_tools(ask_endpoints_client):
    client, agency_id, _pool = ask_endpoints_client
    r = await client.get(f"/api/{agency_id}/ask/build-schema")
    assert r.status_code == 200
    body = r.json()
    assert "tools" in body
    tool_names = {t["name"] for t in body["tools"]}
    # Must include build-suitable tools
    assert {"top_n", "time_series", "compare_segments", "route_stats", "describe_data"} <= tool_names
    # Must exclude non-builder tools
    assert "capabilities" not in tool_names
    assert "route_meta" not in tool_names
    # top_n must declare metric, n, etc.
    top_n = next(t for t in body["tools"] if t["name"] == "top_n")
    field_keys = {f["key"] for f in top_n["fields"]}
    assert {"metric", "n"} <= field_keys


@pytest.mark.asyncio
async def test_build_schema_top_n_has_labels(ask_endpoints_client):
    client, agency_id, _pool = ask_endpoints_client
    r = await client.get(f"/api/{agency_id}/ask/build-schema")
    assert r.status_code == 200
    top_n = next(t for t in r.json()["tools"] if t["name"] == "top_n")
    assert "label_ja" in top_n
    assert "label_en" in top_n


@pytest.mark.asyncio
async def test_edit_action_writes_user_action(ask_endpoints_client):
    client, agency_id, pool = ask_endpoints_client
    sig_hash = "deadbeefdeadbeef"
    async with pool.acquire() as c:
        await c.execute(
            """INSERT INTO ask_intent_cache (signature_hash, tool, args, confidence,
               hit_count, last_question, agency_id) VALUES ($1,'top_n','{}',0.9,1,'Q',$2)""",
            sig_hash,
            agency_id,
        )
    r = await client.post(
        f"/api/{agency_id}/ask/edit-action",
        json={"signature_hash": sig_hash, "action": "edited"},
        headers=_CSRF_HEADERS,
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    async with pool.acquire() as c:
        action = await c.fetchval(
            "SELECT last_user_action FROM ask_intent_cache WHERE signature_hash=$1 AND agency_id=$2",
            sig_hash,
            agency_id,
        )
    assert action == "edited"


@pytest.mark.asyncio
async def test_edit_action_confirmed(ask_endpoints_client):
    client, agency_id, pool = ask_endpoints_client
    sig_hash = "cafebabecafebabe"
    async with pool.acquire() as c:
        await c.execute(
            """INSERT INTO ask_intent_cache (signature_hash, tool, args, confidence,
               hit_count, last_question, agency_id) VALUES ($1,'top_n','{}',0.9,1,'Q',$2)""",
            sig_hash,
            agency_id,
        )
    r = await client.post(
        f"/api/{agency_id}/ask/edit-action",
        json={"signature_hash": sig_hash, "action": "confirmed"},
        headers=_CSRF_HEADERS,
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True}


@pytest.mark.asyncio
async def test_edit_action_rejects_bad_action(ask_endpoints_client):
    client, agency_id, _pool = ask_endpoints_client
    r = await client.post(
        f"/api/{agency_id}/ask/edit-action",
        json={"signature_hash": "0000000000000000", "action": "garbage"},
        headers=_CSRF_HEADERS,
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_suggest_empty_q_returns_list(ask_endpoints_client):
    """Smoke test: empty q returns 200 with a list (may be empty if no chunks seeded)."""
    client, agency_id, _pool = ask_endpoints_client
    r = await client.get(f"/api/{agency_id}/ask/suggest?q=")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)


@pytest.mark.asyncio
async def test_suggest_clamps_limit(ask_endpoints_client, monkeypatch):
    client, agency_id, _pool = ask_endpoints_client

    # Patch the embedder so we get deterministic behavior without needing
    # a real model. Route to the NN path with empty results.
    async def fake_nearest(conn, aid, qvec, k=3):
        return []

    monkeypatch.setattr("api.routers.ask.rag_nearest", fake_nearest)

    r = await client.get(f"/api/{agency_id}/ask/suggest?q=foo&limit=999")
    assert r.status_code == 200
    assert len(r.json()) <= 12


@pytest.mark.asyncio
async def test_suggest_unknown_agency(ask_endpoints_client):
    client, _agency_id, _pool = ask_endpoints_client
    r = await client.get("/api/99999/ask/suggest?q=test")
    assert r.status_code == 404
