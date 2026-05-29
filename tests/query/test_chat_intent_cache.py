"""Cache + JSON-signature integration tests for chat_with_tools."""

from __future__ import annotations

import os
from datetime import date
from types import SimpleNamespace

import asyncpg
import pytest

from api.range import RangeCtx
from pipeline.query import chat as chat_module
from pipeline.query.chat import chat_with_tools

DATABASE_URL = os.environ["DATABASE_URL"]


def _ctx() -> RangeCtx:
    return RangeCtx(from_date=date(2026, 5, 13), to_date=date(2026, 5, 27))


def _sig_message(tool="top_n", args=None, conf=0.85, rationale=""):
    """Build a fake LLM message in JSON-mode shape: content carries the signature JSON, no tool_calls."""
    import json

    payload = {
        "tool": tool,
        "args": args or {"metric": "avg_delay"},
        "confidence": conf,
        "rationale": rationale,
    }
    return SimpleNamespace(content=json.dumps(payload), tool_calls=None)


class _FakeClient:
    """Stand-in for LLMClient.chat_completions returning a canned JSON signature."""

    def __init__(self, message):
        self._message = message
        self.calls = 0

    def chat_completions(self, **kw):
        self.calls += 1
        return self._message, None


@pytest.fixture
async def pool_with_agency(apply_schema):
    """Pool + agency_id + a route so describe_data + tool dispatch can run."""
    pool = await asyncpg.create_pool(DATABASE_URL)
    async with pool.acquire() as c:
        await c.execute("DELETE FROM ask_intent_cache")
        await c.execute("DELETE FROM ask_query_log")
        row = await c.fetchrow(
            "INSERT INTO agencies (agency_name, feed_url) VALUES ('T', 'http://t') RETURNING agency_id"
        )
        agency_id = row["agency_id"]
        await c.execute(
            "INSERT INTO static_routes (agency_id, route_id, route_short_name) VALUES ($1, '系統16071', '16071')",
            agency_id,
        )
    yield pool, agency_id
    async with pool.acquire() as c:
        await c.execute("DELETE FROM ask_intent_cache")
        await c.execute("TRUNCATE agencies CASCADE")
    await pool.close()


@pytest.mark.asyncio
async def test_cache_miss_writes_cache_row(pool_with_agency, monkeypatch):
    """First time a question reaches Stage 3 → cache row written with hit_count=1."""
    pool, agency_id = pool_with_agency
    monkeypatch.setenv("ASK_INTENT_CACHE_ENABLED", "true")
    fake = _FakeClient(_sig_message(tool="capabilities", args={}))
    monkeypatch.setattr(chat_module, "_get_client", lambda: fake)

    async with pool.acquire() as conn:
        await chat_with_tools("路線一覧", _ctx(), conn, agency_id, locale="ja")

    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT signature_hash, tool, hit_count FROM ask_intent_cache")
    assert len(rows) == 1
    assert rows[0]["tool"] == "capabilities"
    assert rows[0]["hit_count"] == 1


@pytest.mark.asyncio
async def test_cache_hit_skips_llm(pool_with_agency, monkeypatch):
    """Second call with same canonical intent hits the cache → LLM is NOT called a second time.

    Both questions map to the same sig_hash (capabilities/{}).  The first call
    writes the cache row; the second call finds it by sig_hash before the LLM
    is invoked and dispatches from cache without calling the LLM.
    """
    pool, agency_id = pool_with_agency
    monkeypatch.setenv("ASK_INTENT_CACHE_ENABLED", "true")
    fake = _FakeClient(_sig_message(tool="capabilities", args={}))
    monkeypatch.setattr(chat_module, "_get_client", lambda: fake)

    # Pre-populate the cache with a row that both subsequent calls will hit.
    # We directly insert the known sig_hash so the second chat_with_tools call
    # can find it in the pre-LLM lookup.
    from pipeline.query.intent import IntentSignature, canonicalize
    from pipeline.query.intent import signature_hash as _sig_hash

    _tool = "capabilities"
    _args = {}
    _ctx_dict = {"from_date": _ctx().from_date, "to_date": _ctx().to_date}
    _can_args = canonicalize(_tool, _args, _ctx_dict)
    _hash = _sig_hash(_tool, _can_args)
    _sig = IntentSignature(tool=_tool, args=_args, confidence=0.85)

    from pipeline.query import intent_cache as ic

    async with pool.acquire() as conn:
        # Insert the first call manually so hit_count starts at 1.
        await ic.upsert(conn, _hash, _sig, _can_args, agency_id, question="路線一覧")

    # Now make two calls with the same question; the cache row already exists.
    async with pool.acquire() as conn:
        await chat_with_tools("路線一覧", _ctx(), conn, agency_id, locale="ja")
        await chat_with_tools("路線一覧", _ctx(), conn, agency_id, locale="ja")

    assert fake.calls == 0, "Both calls must hit the pre-populated cache; LLM must NOT be called"
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT hit_count FROM ask_intent_cache LIMIT 1")
    assert row["hit_count"] == 3  # 1 (manual) + 2 (two cache hits)


@pytest.mark.asyncio
async def test_flag_off_no_cache_reads_or_writes(pool_with_agency, monkeypatch):
    """ASK_INTENT_CACHE_ENABLED=false → no cache table activity, behavior matches Phase ①."""
    pool, agency_id = pool_with_agency
    monkeypatch.setenv("ASK_INTENT_CACHE_ENABLED", "false")
    # Use the tool_calls path (Phase-① shape): build a message with tool_calls, not signature JSON.
    func = SimpleNamespace(name="capabilities", arguments="{}")
    call = SimpleNamespace(function=func, id="call_1", type="function")
    msg = SimpleNamespace(content=None, tool_calls=[call])
    monkeypatch.setattr(chat_module, "_get_client", lambda: _FakeClient(msg))

    async with pool.acquire() as conn:
        result = await chat_with_tools("Q", _ctx(), conn, agency_id, locale="ja")
    assert result["tool_call"] is not None  # tool dispatched fine
    async with pool.acquire() as conn:
        count = await conn.fetchval("SELECT COUNT(*) FROM ask_intent_cache")
    assert count == 0
