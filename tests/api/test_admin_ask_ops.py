"""Tests for GET/POST /api/admin/ask/* — admin Ask-ops query log, funnel,
promote-to-intent-cache, and eval-result endpoints.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from httpx import ASGITransport

from api.security import token_hash
from tests.conftest import _test_pool

_EMBED_DIM = 384  # matches rag_chunks.embedding vector(384)


class _FakeEmbedder:
    available = True
    dim = _EMBED_DIM

    def embed(self, text: str, *, mode: str) -> list[float]:
        return [0.1] * _EMBED_DIM


class _UnavailableEmbedder:
    available = False

    def embed(self, text: str, *, mode: str) -> list[float]:
        raise RuntimeError("unavailable")


async def _seed_admin_session(conn) -> tuple[int, str]:
    uid = (
        await conn.fetchrow(
            "INSERT INTO users (email, role) VALUES ($1, 'admin') RETURNING user_id",
            f"askopsadmin{datetime.now().timestamp()}@x",
        )
    )["user_id"]
    sid = f"sid-askops-{uid}"
    await conn.execute(
        "INSERT INTO sessions (sid_hash, user_id, expires_at) VALUES ($1, $2, $3)",
        token_hash(sid),
        uid,
        datetime.now(timezone.utc) + timedelta(days=1),
    )
    return uid, sid


@pytest.fixture
async def ask_ops_client(apply_schema, monkeypatch):
    from api.main import app
    from api.routers import admin_ask as admin_ask_mod

    monkeypatch.setattr(admin_ask_mod, "get_embedder", lambda: _FakeEmbedder())

    pool = await _test_pool()
    app.state.pool = pool
    async with pool.acquire() as conn:
        await conn.execute(
            "TRUNCATE agencies, sessions, users, ask_query_log, ask_intent_cache, rag_chunks, admin_audit CASCADE"
        )
        admin_uid, admin_sid = await _seed_admin_session(conn)
        agency_id = (
            await conn.fetchrow(
                "INSERT INTO agencies (agency_name, feed_url) VALUES ('AskOpsAgency', 'http://x') RETURNING agency_id"
            )
        )["agency_id"]
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c, admin_sid, admin_uid, agency_id, pool
    await pool.close()


async def _insert_log(
    pool,
    agency_id,
    *,
    router_stage="llm",
    tool="top_n",
    success=True,
    signature_hash=None,
    question="遅延が大きい路線は?",
):
    async with pool.acquire() as c:
        row = await c.fetchrow(
            """
            INSERT INTO ask_query_log (agency_id, question, router_stage, tool, success, signature_hash)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING id
            """,
            agency_id,
            question,
            router_stage,
            tool,
            success,
            signature_hash,
        )
    return row["id"]


@pytest.mark.asyncio
async def test_queries_requires_admin(ask_ops_client):
    c, *_ = ask_ops_client
    resp = await c.get("/api/admin/ask/queries")
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_queries_returns_derived_route_and_status(ask_ops_client):
    c, sid, _uid, agency_id, pool = ask_ops_client
    await _insert_log(pool, agency_id, router_stage="rules", success=True)
    await _insert_log(pool, agency_id, router_stage="embedding", success=False)
    await _insert_log(pool, agency_id, router_stage="llm", success=True, signature_hash="aaaaaaaaaaaaaaaa")

    resp = await c.get("/api/admin/ask/queries", cookies={"sid": sid})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["rows"]) == 3
    by_route = {r["route"]: r for r in body["rows"]}
    assert by_route["rules"]["status"] == "ok"
    assert by_route["nn"]["status"] == "error"
    assert by_route["rag"]["promotable"] is True
    assert by_route["rules"]["promotable"] is False
    assert "user" not in body["rows"][0]
    assert "latency_ms" not in body["rows"][0]


@pytest.mark.asyncio
async def test_queries_filters_by_route_and_status(ask_ops_client):
    c, sid, _uid, agency_id, pool = ask_ops_client
    await _insert_log(pool, agency_id, router_stage="rules", success=True)
    await _insert_log(pool, agency_id, router_stage="llm", success=False, signature_hash="bbbbbbbbbbbbbbbb")

    resp = await c.get("/api/admin/ask/queries", params={"route": "rag"}, cookies={"sid": sid})
    body = resp.json()
    assert len(body["rows"]) == 1
    assert body["rows"][0]["route"] == "rag"

    resp = await c.get("/api/admin/ask/queries", params={"status": "error"}, cookies={"sid": sid})
    body = resp.json()
    assert len(body["rows"]) == 1
    assert body["rows"][0]["status"] == "error"


@pytest.mark.asyncio
async def test_queries_rejects_unknown_route_and_status(ask_ops_client):
    c, sid, *_ = ask_ops_client
    resp = await c.get("/api/admin/ask/queries", params={"route": "bogus"}, cookies={"sid": sid})
    assert resp.status_code == 400
    resp = await c.get("/api/admin/ask/queries", params={"status": "bogus"}, cookies={"sid": sid})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_queries_cursor_pagination(ask_ops_client):
    c, sid, _uid, agency_id, pool = ask_ops_client
    for _ in range(3):
        await _insert_log(pool, agency_id)

    resp = await c.get("/api/admin/ask/queries", params={"limit": 2}, cookies={"sid": sid})
    body = resp.json()
    assert len(body["rows"]) == 2
    assert body["next_cursor"] is not None

    resp2 = await c.get(
        "/api/admin/ask/queries", params={"limit": 2, "cursor": body["next_cursor"]}, cookies={"sid": sid}
    )
    body2 = resp2.json()
    assert len(body2["rows"]) == 1
    assert body2["next_cursor"] is None
    seen_ids = {r["id"] for r in body["rows"]} | {r["id"] for r in body2["rows"]}
    assert len(seen_ids) == 3


@pytest.mark.asyncio
async def test_funnel_requires_admin(ask_ops_client):
    c, *_ = ask_ops_client
    resp = await c.get("/api/admin/ask/funnel")
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_funnel_aggregates_by_route_and_omits_providers(ask_ops_client):
    c, sid, _uid, agency_id, pool = ask_ops_client
    await _insert_log(pool, agency_id, router_stage="rules", success=True)
    await _insert_log(pool, agency_id, router_stage="rules", success=True)
    await _insert_log(pool, agency_id, router_stage="llm", success=False, signature_hash="cccccccccccccccc")

    resp = await c.get("/api/admin/ask/funnel", cookies={"sid": sid})
    assert resp.status_code == 200
    body = resp.json()
    assert body["providers"] is None
    by_route = {r["route"]: r for r in body["by_route"]}
    assert by_route["rules"]["count"] == 2
    assert by_route["rules"]["success_count"] == 2
    assert by_route["rag"]["count"] == 1
    assert by_route["rag"]["success_count"] == 0
    assert body["total"] == 3


@pytest.mark.asyncio
async def test_promote_requires_admin(ask_ops_client):
    c, *_ = ask_ops_client
    resp = await c.post("/api/admin/ask/promote", json={"query_log_id": 1})
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_promote_404_for_missing_log_row(ask_ops_client):
    c, sid, *_ = ask_ops_client
    resp = await c.post(
        "/api/admin/ask/promote",
        json={"query_log_id": 999999},
        cookies={"sid": sid},
        headers={"Origin": "http://test"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_promote_400_for_non_rag_row(ask_ops_client):
    c, sid, _uid, agency_id, pool = ask_ops_client
    log_id = await _insert_log(pool, agency_id, router_stage="rules", signature_hash=None)
    resp = await c.post(
        "/api/admin/ask/promote",
        json={"query_log_id": log_id},
        cookies={"sid": sid},
        headers={"Origin": "http://test"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_promote_503_when_embedder_unavailable(ask_ops_client, monkeypatch):
    from api.routers import admin_ask as admin_ask_mod

    monkeypatch.setattr(admin_ask_mod, "get_embedder", lambda: _UnavailableEmbedder())
    c, sid, _uid, agency_id, pool = ask_ops_client
    async with pool.acquire() as conn:
        from pipeline.query import intent_cache
        from pipeline.query.intent import IntentSignature

        await intent_cache.upsert(
            conn,
            "dddddddddddddddd",
            IntentSignature(tool="top_n", args={}, confidence=0.9),
            {},
            agency_id,
            question="Q",
        )
    log_id = await _insert_log(pool, agency_id, router_stage="llm", signature_hash="dddddddddddddddd")
    resp = await c.post(
        "/api/admin/ask/promote",
        json={"query_log_id": log_id},
        cookies={"sid": sid},
        headers={"Origin": "http://test"},
    )
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_promote_success_marks_promoted_and_inserts_chunk(ask_ops_client):
    c, sid, _uid, agency_id, pool = ask_ops_client
    async with pool.acquire() as conn:
        from pipeline.query import intent_cache
        from pipeline.query.intent import IntentSignature

        await intent_cache.upsert(
            conn,
            "eeeeeeeeeeeeeeee",
            IntentSignature(tool="top_n", args={"metric": "avg_delay"}, confidence=0.9),
            {"metric": "avg_delay"},
            agency_id,
            question="遅延ランキング",
        )
    log_id = await _insert_log(
        pool, agency_id, router_stage="llm", signature_hash="eeeeeeeeeeeeeeee", question="遅延ランキング"
    )

    resp = await c.post(
        "/api/admin/ask/promote",
        json={"query_log_id": log_id},
        cookies={"sid": sid},
        headers={"Origin": "http://test"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["promoted"] is True
    assert body["chunk_id"] == "cache_eeeeeeeeeeeeeeee"

    async with pool.acquire() as conn:
        promoted_at = await conn.fetchval(
            "SELECT promoted_at FROM ask_intent_cache WHERE signature_hash=$1", "eeeeeeeeeeeeeeee"
        )
        assert promoted_at is not None
        chunk = await conn.fetchrow("SELECT content FROM rag_chunks WHERE chunk_id='cache_eeeeeeeeeeeeeeee'")
        assert chunk is not None
        assert chunk["content"] == "遅延ランキング"
        audit = await conn.fetchrow(
            "SELECT actor_id, target_type, target_id, after FROM admin_audit WHERE action='ask.promote_intent_cache'"
        )
        assert audit is not None
        assert audit["actor_id"] == _uid
        assert audit["target_type"] == "intent_cache"
        assert audit["target_id"] == "eeeeeeeeeeeeeeee"
        assert json.loads(audit["after"]) == {"agency_id": agency_id, "query_log_id": log_id}


@pytest.mark.asyncio
async def test_promote_idempotent_returns_already_promoted(ask_ops_client):
    c, sid, _uid, agency_id, pool = ask_ops_client
    async with pool.acquire() as conn:
        from pipeline.query import intent_cache
        from pipeline.query.intent import IntentSignature

        await intent_cache.upsert(
            conn,
            "ffffffffffffffff",
            IntentSignature(tool="top_n", args={}, confidence=0.9),
            {},
            agency_id,
            question="Q2",
        )
    log_id = await _insert_log(pool, agency_id, router_stage="llm", signature_hash="ffffffffffffffff", question="Q2")

    first = await c.post(
        "/api/admin/ask/promote",
        json={"query_log_id": log_id},
        cookies={"sid": sid},
        headers={"Origin": "http://test"},
    )
    assert first.json()["promoted"] is True

    second = await c.post(
        "/api/admin/ask/promote",
        json={"query_log_id": log_id},
        cookies={"sid": sid},
        headers={"Origin": "http://test"},
    )
    assert second.status_code == 200
    assert second.json() == {"promoted": False, "reason": "already_promoted", "chunk_id": None}


@pytest.mark.asyncio
async def test_eval_requires_admin(ask_ops_client):
    c, *_ = ask_ops_client
    resp = await c.get("/api/admin/ask/eval")
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_eval_returns_null_when_no_artifact(ask_ops_client, monkeypatch, tmp_path):
    from api.routers import admin_ask as admin_ask_mod

    monkeypatch.setattr(admin_ask_mod, "_EVAL_CACHE_DIR", tmp_path / "no-such-dir")
    c, sid, *_ = ask_ops_client
    resp = await c.get("/api/admin/ask/eval", cookies={"sid": sid})
    assert resp.status_code == 200
    assert resp.json() is None


@pytest.mark.asyncio
async def test_eval_returns_latest_artifact_when_present(ask_ops_client, monkeypatch, tmp_path):
    from api.routers import admin_ask as admin_ask_mod

    (tmp_path / "ask-eval-2026-09-14.json").write_text('{"score": 0.87}', encoding="utf-8")
    monkeypatch.setattr(admin_ask_mod, "_EVAL_CACHE_DIR", tmp_path)
    c, sid, *_ = ask_ops_client
    resp = await c.get("/api/admin/ask/eval", cookies={"sid": sid})
    assert resp.status_code == 200
    assert resp.json()["score"] == 0.87
