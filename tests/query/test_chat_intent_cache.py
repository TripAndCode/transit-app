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
async def test_force_tool_call_appends_json_mode_directive_under_cache(pool_with_agency, monkeypatch):
    """item 8 review finding: force_tool_call was a silent no-op under
    ASK_INTENT_CACHE_ENABLED, since JSON mode (response_format=json_object)
    can't be combined with tool_choice at all — the fix only reached the
    non-cache branch. Pins that force_tool_call=True instead appends
    JSON_MODE_FORCE_TOOL_ADDENDUM's prompt-level directive so the model is
    told it must resolve to a real tool, not null/omitted, even in
    cache-enabled deployments.
    """
    pool, agency_id = pool_with_agency
    monkeypatch.setenv("ASK_INTENT_CACHE_ENABLED", "true")
    captured = {}

    class _CapturingClient:
        def chat_completions(self, *, messages, **kw):
            captured["system_prompt"] = messages[0]["content"]
            return _sig_message(tool="capabilities", args={}), None

    monkeypatch.setattr(chat_module, "_get_client", lambda: _CapturingClient())

    async with pool.acquire() as conn:
        await chat_with_tools(
            "次の50件",
            _ctx(),
            conn,
            agency_id,
            locale="ja",
            history=[{"question": "路線一覧", "tool": "capabilities", "args": {}}],
            force_tool_call=True,
        )
    assert "MUST name that same tool" in captured["system_prompt"]

    async with pool.acquire() as conn:
        await chat_with_tools("別の質問です", _ctx(), conn, agency_id, locale="ja", force_tool_call=False)
    assert "MUST name that same tool" not in captured["system_prompt"]


@pytest.mark.asyncio
async def test_cache_hit_skips_llm(pool_with_agency, monkeypatch):
    """Same-text repeats hit the pre-LLM question-text lookup → LLM never invoked.

    Exercises the fast path: when the exact question text is in the cache,
    we skip the LLM entirely via lookup_by_question. (The paraphrase path
    — different text, same canonical signature — is covered by
    test_cache_paraphrase_collapses_to_same_dispatch below.)
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
async def test_force_tool_call_skips_stale_cache_pre_hit(pool_with_agency, monkeypatch):
    """The Stage-1 exact-text pre-hit is keyed only on literal question text
    + agency, with no notion of ``history`` — so a
    generic continuation phrase like "次の50件" would return whichever
    (tool, args) was last cached for that exact text by *any* prior question
    in the agency, ignoring the current conversation's actual prior turn.
    When force_tool_call=True (a recognized, history-dependent
    continuation), the pre-hit must be skipped so the LLM call — which
    actually sees history_block — decides instead of a stale cache row.
    """
    pool, agency_id = pool_with_agency
    monkeypatch.setenv("ASK_INTENT_CACHE_ENABLED", "true")

    from pipeline.query import intent_cache as ic
    from pipeline.query.intent import IntentSignature, canonicalize
    from pipeline.query.intent import signature_hash as _sig_hash

    # Pre-populate a stale cache row for the exact literal text "次の50件",
    # from some unrelated earlier question/tool — this is what a naive
    # pre-hit lookup would return regardless of the current history.
    _stale_tool, _stale_args = "capabilities", {}
    _ctx_dict = {"from_date": _ctx().from_date, "to_date": _ctx().to_date}
    _stale_can_args = canonicalize(_stale_tool, _stale_args, _ctx_dict)
    _stale_hash = _sig_hash(_stale_tool, _stale_can_args)
    async with pool.acquire() as conn:
        await ic.upsert(
            conn,
            _stale_hash,
            IntentSignature(tool=_stale_tool, args=_stale_args, confidence=0.9),
            _stale_can_args,
            agency_id,
            question="次の50件",
        )

    fake = _FakeClient(_sig_message(tool="describe_data", args={"kind": "stops", "offset": 50}))
    monkeypatch.setattr(chat_module, "_get_client", lambda: fake)

    async with pool.acquire() as conn:
        result = await chat_with_tools(
            "次の50件",
            _ctx(),
            conn,
            agency_id,
            locale="ja",
            history=[{"question": "停留所はいくつ？", "tool": "describe_data", "args": {"kind": "stops"}}],
            force_tool_call=True,
        )

    assert fake.calls == 1, "force_tool_call must skip the stale pre-hit and reach the LLM"
    assert result["tool_call"]["name"] == "describe_data"
    assert result["tool_call"]["arguments"].get("offset") == 50


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


@pytest.mark.asyncio
async def test_build_sentinel_short_circuits_llm(pool_with_agency, monkeypatch):
    """A `__build__ tool {json}` question dispatches directly without any LLM call.

    Confidence is 1.0, cache_outcome is "bypass", and no cache row is written.
    """
    pool, agency_id = pool_with_agency
    monkeypatch.setenv("ASK_INTENT_CACHE_ENABLED", "true")

    class _NoCallClient:
        def __init__(self):
            self.calls = 0

        def chat_completions(self, **kw):
            self.calls += 1
            raise AssertionError("LLM must NOT be called for build-mode sentinels")

    fake = _NoCallClient()
    monkeypatch.setattr(chat_module, "_get_client", lambda: fake)

    async with pool.acquire() as conn:
        result = await chat_with_tools("__build__ capabilities {}", _ctx(), conn, agency_id, locale="ja")
    assert fake.calls == 0
    assert result["confidence"] == 1.0
    assert result["cache_outcome"] == "bypass"
    assert result["signature_hash"] is not None
    assert result["tool_call"]["name"] == "capabilities"
    # And no cache row was written
    async with pool.acquire() as conn:
        count = await conn.fetchval("SELECT count(*) FROM ask_intent_cache")
    assert count == 0


@pytest.mark.asyncio
async def test_cache_paraphrase_collapses_to_same_dispatch(pool_with_agency, monkeypatch):
    """The spec's core promise: two differently-worded questions that emit the
    same canonical IntentSignature collapse to one cache row.

    The LLM IS called for each novel phrasing (the pre-LLM text lookup misses
    because the wording differs), but the post-LLM sig_hash lookup hits on
    the second call so the dispatch comes from the cache row written by the
    first call. ``hit_count`` advances to 2; cache_outcome is "miss" then "hit".
    """
    pool, agency_id = pool_with_agency
    monkeypatch.setenv("ASK_INTENT_CACHE_ENABLED", "true")
    # Both calls return the SAME tool+args (same canonical signature) but
    # come back from a fake client we reset between calls to count invocations.
    msg = _sig_message(tool="capabilities", args={})

    class _Counter:
        def __init__(self):
            self.calls = 0

        def chat_completions(self, **kw):
            self.calls += 1
            return msg, None

    counter = _Counter()
    monkeypatch.setattr(chat_module, "_get_client", lambda: counter)

    async with pool.acquire() as conn:
        r1 = await chat_with_tools("路線を教えて", _ctx(), conn, agency_id, locale="ja")
        r2 = await chat_with_tools("どんな路線がある？", _ctx(), conn, agency_id, locale="ja")

    assert counter.calls == 2, "Different texts → LLM called once per phrasing (pre-LLM text lookup misses)"
    assert r1["cache_outcome"] == "miss"
    assert r2["cache_outcome"] == "hit", "Same canonical signature → second call hits the cache"
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT signature_hash, hit_count FROM ask_intent_cache")
    assert len(rows) == 1, "Both calls share one cache row (same sig_hash)"
    assert rows[0]["hit_count"] == 2
