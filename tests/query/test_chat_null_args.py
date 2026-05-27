"""Regression test: chat_with_tools must survive non-dict ``arguments`` JSON.

Some LLMs occasionally emit ``arguments`` as a non-object JSON value —
``null``, a bare string like ``'"foo"'``, or an array like ``'[]'`` —
rather than the documented JSON object. Until the chat normaliser was
hardened, any of those crashed the downstream ``args.get(...)`` call in
the tool handler and surfaced a 500. This test pins the orchestrator's
real behaviour end-to-end by monkeypatching the LLM adapter so we
control the exact ``arguments`` string the model returns.
"""

import os
from datetime import date
from types import SimpleNamespace

import asyncpg
import pytest

from api.range import RangeCtx
from pipeline.query import chat as chat_module
from pipeline.query.chat import chat_with_tools

DATABASE_URL = os.environ["DATABASE_URL"]


def _fake_message(arguments: str, tool_name: str = "capabilities"):
    """Build the minimal object shape Groq's chat completions return.

    Mirrors ``resp.choices[0].message`` from the openai/Groq SDK so the
    orchestrator's ``getattr(msg, 'tool_calls', None)`` and
    ``call.function.arguments`` reads both resolve cleanly.
    """
    func = SimpleNamespace(name=tool_name, arguments=arguments)
    call = SimpleNamespace(function=func, id="call_1", type="function")
    return SimpleNamespace(content=None, tool_calls=[call])


class _FakeClient:
    """Stand-in for :class:`pipeline.query.llm_client.LLMClient`.

    ``chat_with_tools`` calls ``client.chat_completions(...)`` on whatever
    ``_get_client()`` returns; this fake skips the provider ladder and
    returns a canned message directly.
    """

    def __init__(self, arguments: str, tool_name: str = "capabilities"):
        self._arguments = arguments
        self._tool_name = tool_name

    def chat_completions(self, **kwargs):  # noqa: D401 — adapter shape
        return _fake_message(self._arguments, self._tool_name)


def _ctx() -> RangeCtx:
    return RangeCtx(from_date=date(2026, 5, 1), to_date=date(2026, 5, 26))


@pytest.fixture
async def conn_with_minimal_seed(apply_schema):
    """Pool + agency_id with one route so capabilities/describe_data have
    something to dispatch against."""
    pool = await asyncpg.create_pool(DATABASE_URL)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO agencies (agency_name, feed_url) VALUES ('T', 'http://t') RETURNING agency_id"
        )
        agency_id = row["agency_id"]
        await conn.execute(
            "INSERT INTO static_routes (agency_id, route_id, route_short_name) VALUES ($1, '国道線(1021)', 'A1')",
            agency_id,
        )
    yield pool, agency_id
    async with pool.acquire() as c:
        await c.execute("TRUNCATE agencies CASCADE")
    await pool.close()


@pytest.mark.parametrize("bad_args", ["null", '"foo"', "[]", "42"])
@pytest.mark.asyncio
async def test_chat_with_tools_survives_non_dict_arguments(
    bad_args,
    conn_with_minimal_seed,
    monkeypatch,
):
    """The orchestrator must coerce any non-dict ``arguments`` JSON to ``{}``
    and still dispatch the tool — no AttributeError, no 500, no crash."""
    pool, agency_id = conn_with_minimal_seed
    # capabilities has no required args, so an empty dict is a valid
    # invocation and the dispatch path returns kind='kv' (not an error).
    monkeypatch.setattr(chat_module, "_get_client", lambda: _FakeClient(bad_args, "capabilities"))

    async with pool.acquire() as conn:
        result = await chat_with_tools("なんでもいい", _ctx(), conn, agency_id, locale="ja")

    # No exception, structured response, and the recorded args are a dict.
    assert result["tool_call"] is not None
    assert result["tool_call"]["name"] == "capabilities"
    assert isinstance(result["tool_call"]["arguments"], dict)
    # The capabilities tool returns kv pairs; ensure the orchestrator
    # ran the dispatch path (not the refusal fallback).
    assert result["result"] is not None
    assert result["result"]["kind"] == "kv"


@pytest.mark.asyncio
async def test_rag_examples_appended_to_system_prompt(monkeypatch):
    """When chat_with_tools receives rag_examples, the system prompt includes them."""
    from types import SimpleNamespace

    from pipeline.query import chat
    from pipeline.query.rag_index import Match

    captured = {}

    class _FakeClient:
        def chat_completions(self, *, messages, tools, tool_choice, temperature, model_override):
            captured["messages"] = messages
            return SimpleNamespace(content="ok", tool_calls=None)

    monkeypatch.setattr(chat, "get_client", lambda: _FakeClient())

    from datetime import date

    from api.range import RangeCtx

    examples = [
        Match(chunk_id="g-1", content="中央大橋線の遅延", tool="route_stats", args={"route": "12211"}, distance=0.05),
        Match(chunk_id="g-2", content="国道線の傾向", tool="time_series", args={}, distance=0.10),
    ]
    ctx = RangeCtx(from_date=date(2026, 5, 1), to_date=date(2026, 5, 27))
    await chat.chat_with_tools(
        "もっと変な質問", ctx, conn=None, agency_id=1, model=None, locale="ja", rag_examples=examples
    )

    system = captured["messages"][0]["content"]
    assert "中央大橋線の遅延" in system
    assert "route_stats" in system
    assert "国道線の傾向" in system


@pytest.mark.asyncio
async def test_history_injected_into_prompt(monkeypatch):
    """When history is supplied, prior turns appear in the prompt messages."""
    from types import SimpleNamespace

    from pipeline.query import chat

    captured = {}

    class _FakeClient:
        def chat_completions(self, *, messages, tools, tool_choice, temperature, model_override):
            captured["messages"] = messages
            return SimpleNamespace(content="ok", tool_calls=None)

    monkeypatch.setattr(chat, "_get_client", lambda: _FakeClient())

    from datetime import date

    from api.range import RangeCtx

    ctx = RangeCtx(from_date=date(2026, 5, 1), to_date=date(2026, 5, 27))
    history = [{"question": "停留所はいくつ？", "tool": "describe_data", "args": {"kind": "stops"}}]
    await chat.chat_with_tools("次の50件", ctx, conn=None, agency_id=1, history=history)

    blob = " ".join(m["content"] for m in captured["messages"])
    assert "停留所はいくつ？" in blob
    assert "describe_data" in blob


@pytest.mark.asyncio
async def test_empty_history_matches_no_history(monkeypatch):
    """history=[] must produce the same messages as history=None."""
    from types import SimpleNamespace

    from pipeline.query import chat

    seen = []

    class _FakeClient:
        def chat_completions(self, *, messages, **k):
            seen.append([m["content"] for m in messages])
            return SimpleNamespace(content="ok", tool_calls=None)

    monkeypatch.setattr(chat, "_get_client", lambda: _FakeClient())

    from datetime import date

    from api.range import RangeCtx

    ctx = RangeCtx(from_date=date(2026, 5, 1), to_date=date(2026, 5, 27))
    await chat.chat_with_tools("質問", ctx, conn=None, agency_id=1, history=None)
    await chat.chat_with_tools("質問", ctx, conn=None, agency_id=1, history=[])
    assert seen[0] == seen[1]
