"""Regression test: chat_with_tools must survive non-dict ``arguments`` JSON.

Some LLMs occasionally emit ``arguments`` as a non-object JSON value —
``null``, a bare string like ``'"foo"'``, or an array like ``'[]'`` —
rather than the documented JSON object. Unless the chat normaliser coerces
them, any of those crashes the downstream ``args.get(...)`` call in the
tool handler and surfaces a 500. This test pins the orchestrator's
real behaviour end-to-end by monkeypatching the LLM adapter so we
control the exact ``arguments`` string the model returns.
"""

from datetime import date
from types import SimpleNamespace

import pytest

from api.range import RangeCtx
from pipeline.query import chat as chat_module
from pipeline.query.chat import chat_with_tools
from tests.conftest import _test_pool


def _fake_message(arguments: str, tool_name: str = "capabilities"):
    """Build the minimal object shape an OpenAI-compatible chat completion returns.

    Mirrors ``resp.choices[0].message`` from the openai SDK so the
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
    returns ``(message, None)`` matching the real adapter's tuple contract.
    """

    def __init__(self, arguments: str, tool_name: str = "capabilities"):
        self._arguments = arguments
        self._tool_name = tool_name

    def chat_completions(self, **kwargs):
        """Stub returning a canned chat completion."""
        return _fake_message(self._arguments, self._tool_name), None


def _ctx() -> RangeCtx:
    return RangeCtx(from_date=date(2026, 5, 1), to_date=date(2026, 5, 26))


@pytest.fixture
async def conn_with_minimal_seed(apply_schema):
    """Pool + agency_id with one route so capabilities/describe_data have
    something to dispatch against."""
    pool = await _test_pool()
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
async def test_history_block_scopes_use_to_explicit_references(monkeypatch):
    """The history block must tell the model not to answer an unrelated
    question from stale prior-turn data.

    With history from an unrelated ranking turn attached, a plain
    "停留所はいくつ？" ("how many stops?") — on-topic but with no follow-up
    phrasing, so ``match_rules()`` still runs and finds no confident
    match — must dispatch ``describe_data(kind=stops)``. Without an explicit
    instruction the model can answer from the attached history text instead
    ("the table doesn't show stop counts"). Pin that the history block
    carries an explicit "only use this if the current question references
    it" guard, in both locales, so this instruction can't silently regress
    out of the prompt.
    """
    from pipeline.query import chat

    captured = {}

    class _FakeClient:
        def chat_completions(self, *, messages, tools, tool_choice, temperature, allowed_providers=None):
            captured["messages"] = messages
            return SimpleNamespace(content="ok", tool_calls=None), None

    monkeypatch.setattr(chat, "_get_client", lambda: _FakeClient())

    ctx = RangeCtx(from_date=date(2026, 5, 1), to_date=date(2026, 5, 27))
    history = [{"question": "遅延ランキングを見せて", "tool": "top_n", "args": {"metric": "avg_delay", "n": 10}}]

    await chat.chat_with_tools("停留所はいくつ？", ctx, conn=None, agency_id=1, locale="ja", history=history)
    blob_ja = " ".join(m["content"] for m in captured["messages"])
    assert "現在の質問が上記と無関係な新しい話題であれば" in blob_ja
    assert "適切なツールを呼び出す" in blob_ja

    await chat.chat_with_tools("how many stops are there?", ctx, conn=None, agency_id=1, locale="en", history=history)
    blob_en = " ".join(m["content"] for m in captured["messages"])
    assert "new, unrelated topic" in blob_en
    assert "call the appropriate tool for the current question on its own" in blob_en


@pytest.mark.asyncio
async def test_unrelated_question_with_unrelated_history_dispatches_tool(conn_with_minimal_seed, monkeypatch):
    """End to end: an unrelated in-scope question with no follow-up
    phrasing must still be able to dispatch a fresh tool call even though
    unrelated history from a prior ranking turn is attached.

    The scenario: history carries a ``top_n`` ranking turn, the
    current question ("停留所はいくつ？") has no follow-up phrasing (so
    ``force_tool_call`` is False — this is not a recognized continuation,
    see ``api/routers/ask.py``), and the fake LLM plays the role of a
    correctly-instructed model by returning a fresh ``describe_data``
    tool call instead of prose anchored to the unrelated history. This pins
    that the orchestrator's plumbing (dispatch + response shaping) actually
    surfaces that tool call rather than something along the way collapsing
    it to a prose non-answer.
    """
    pool, agency_id = conn_with_minimal_seed

    def _fake_message():
        func = SimpleNamespace(name="describe_data", arguments='{"kind": "stops"}')
        call = SimpleNamespace(function=func, id="call_1", type="function")
        return SimpleNamespace(content=None, tool_calls=[call])

    class _FakeClient:
        def chat_completions(self, *, tool_choice, **k):
            assert tool_choice == "auto"  # not a recognized continuation — must not be forced
            return _fake_message(), None

    monkeypatch.setattr(chat_module, "_get_client", lambda: _FakeClient())

    ctx = _ctx()
    history = [{"question": "遅延ランキングを見せて", "tool": "top_n", "args": {"metric": "avg_delay", "n": 10}}]

    async with pool.acquire() as conn:
        result = await chat_with_tools(
            "停留所はいくつ？",
            ctx,
            conn,
            agency_id,
            locale="ja",
            history=history,
            force_tool_call=False,
        )

    assert result["tool_call"] is not None
    assert result["tool_call"]["name"] == "describe_data"
    assert result["tool_call"]["arguments"] == {"kind": "stops"}
