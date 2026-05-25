"""Regression test: chat_with_tools must survive non-dict ``arguments`` JSON.

Some LLMs occasionally emit ``arguments`` as a non-object JSON value —
``null``, a bare string like ``'"foo"'``, or an array like ``'[]'`` —
rather than the documented JSON object. Until the chat normaliser was
hardened, any of those crashed the downstream ``args.get(...)`` call in
the tool handler and surfaced a 500. This test pins the orchestrator's
real behaviour end-to-end by monkeypatching the Groq client so we
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
    """Stand-in for the Groq SDK client used by ``chat._get_client``.

    We construct the message in advance so each invocation just returns a
    canned response; the ``messages`` / ``tools`` kwargs are ignored.
    """

    def __init__(self, arguments: str, tool_name: str = "capabilities"):
        self._arguments = arguments
        self._tool_name = tool_name

        class _Completions:
            def create(inner_self, **kwargs):  # noqa: N805 — sdk-like signature
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=_fake_message(self._arguments, self._tool_name))]
                )

        self.chat = SimpleNamespace(completions=_Completions())


def _ctx() -> RangeCtx:
    return RangeCtx(from_date=date(2026, 5, 1), to_date=date(2026, 5, 26))


@pytest.fixture
async def conn_with_minimal_seed(apply_schema):
    """Pool + agency_id with one route so capabilities/describe_data have
    something to dispatch against."""
    pool = await asyncpg.create_pool(DATABASE_URL)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO agencies (agency_name, feed_url) VALUES ('T', 'http://t') "
            "RETURNING agency_id"
        )
        agency_id = row["agency_id"]
        await conn.execute(
            "INSERT INTO static_routes (agency_id, route_id, route_short_name) "
            "VALUES ($1, '国道線(1021)', 'A1')",
            agency_id,
        )
    yield pool, agency_id
    async with pool.acquire() as c:
        await c.execute("TRUNCATE agencies CASCADE")
    await pool.close()


@pytest.mark.parametrize("bad_args", ['null', '"foo"', '[]', '42'])
@pytest.mark.asyncio
async def test_chat_with_tools_survives_non_dict_arguments(
    bad_args, conn_with_minimal_seed, monkeypatch,
):
    """The orchestrator must coerce any non-dict ``arguments`` JSON to ``{}``
    and still dispatch the tool — no AttributeError, no 500, no crash."""
    pool, agency_id = conn_with_minimal_seed
    # capabilities has no required args, so an empty dict is a valid
    # invocation and the dispatch path returns kind='kv' (not an error).
    monkeypatch.setattr(
        chat_module, "_get_client", lambda: _FakeClient(bad_args, "capabilities")
    )

    async with pool.acquire() as conn:
        result = await chat_with_tools(
            "なんでもいい", _ctx(), conn, agency_id, locale="ja"
        )

    # No exception, structured response, and the recorded args are a dict.
    assert result["tool_call"] is not None
    assert result["tool_call"]["name"] == "capabilities"
    assert isinstance(result["tool_call"]["arguments"], dict)
    # The capabilities tool returns kv pairs; ensure the orchestrator
    # ran the dispatch path (not the refusal fallback).
    assert result["result"] is not None
    assert result["result"]["kind"] == "kv"
