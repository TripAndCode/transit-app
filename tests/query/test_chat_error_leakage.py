"""Regression: chat_with_tools must never leak raw exception text (e.g. from
clickhouse_connect, or any other backend failure) into the user-facing answer.

Before this fix, every ``except Exception as exc:`` around a ``dispatch(...)``
call in ``pipeline/query/chat.py`` rendered
``_chat_str("tool_error", locale, name=name, exc=exc)`` — interpolating the
raw exception text directly into the chat response. Since this branch ports
``updates`` reads to ClickHouse, ``clickhouse_connect``'s error strings can
include the failing SQL fragment, the server version, and the internal query
endpoint URL — none of which should ever reach an unauthenticated ``/ask``
client. This mirrors the fix already applied to ``api/routers/ask.py``'s
Stage 1/2 dispatch (Fix-8f): ClickHouse-unavailable (HTTPException 503) and
mid-query ClickHouse errors degrade to the existing ``service_unavailable``
locale string (no interpolation); a non-503 HTTPException still propagates;
any other exception falls back to the (now non-interpolating) ``tool_error``
string. All cases must still log the full detail server-side.
"""

import logging
from datetime import date
from types import SimpleNamespace

import clickhouse_connect
import pytest
from fastapi import HTTPException

from api.range import RangeCtx
from pipeline.query import chat

_SECRET = "http://ch-internal.example:8123/?database=transit&param_x=leak SELECT * FROM updates"


def _fake_message(tool_name: str = "describe_data", arguments: str = '{"kind": "routes"}'):
    """Minimal object shape mirroring the Groq/openai SDK's tool_calls response."""
    func = SimpleNamespace(name=tool_name, arguments=arguments)
    call = SimpleNamespace(function=func, id="call_1", type="function")
    return SimpleNamespace(content=None, tool_calls=[call])


class _FakeClient:
    """Stand-in LLM client that always emits one tool call."""

    def chat_completions(self, **kwargs):
        return _fake_message(), None


def _ctx() -> RangeCtx:
    return RangeCtx(from_date=date(2026, 5, 1), to_date=date(2026, 5, 26))


@pytest.mark.asyncio
async def test_clickhouse_unavailable_503_hides_exception_text(monkeypatch):
    """The _ClickHouseUnavailable stand-in's HTTPException(503) must render the
    generic service_unavailable string, never the raw detail."""

    async def _raise_503(*a, **k):
        raise HTTPException(status_code=503, detail=_SECRET)

    monkeypatch.setattr(chat, "_get_client", lambda: _FakeClient())
    monkeypatch.setattr(chat, "dispatch", _raise_503)

    out = await chat.chat_with_tools("質問", _ctx(), conn=None, agency_id=1, locale="ja")

    assert out["success"] is False
    assert _SECRET not in out["answer"]
    assert out["answer"] == chat._chat_str("service_unavailable", "ja", name="describe_data")


@pytest.mark.asyncio
async def test_non_503_http_exception_propagates(monkeypatch):
    """A non-503 HTTPException from dispatch is a real error (e.g. a tool's
    own validation 4xx) and must propagate, not be swallowed into a graceful
    200-shaped answer — mirrors the api/routers/ask.py convention."""

    async def _raise_400(*a, **k):
        raise HTTPException(status_code=400, detail="bad request")

    monkeypatch.setattr(chat, "_get_client", lambda: _FakeClient())
    monkeypatch.setattr(chat, "dispatch", _raise_400)

    with pytest.raises(HTTPException) as exc_info:
        await chat.chat_with_tools("質問", _ctx(), conn=None, agency_id=1, locale="ja")
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_clickhouse_query_error_hides_exception_text(monkeypatch):
    """A mid-query clickhouse_connect error (real client, query failed) must
    also render the generic service_unavailable string — never the raw
    ClickHouse error text, which can include the SQL fragment, server
    version, and query endpoint URL."""

    async def _raise_ch_error(*a, **k):
        raise clickhouse_connect.driver.exceptions.DatabaseError(_SECRET)

    monkeypatch.setattr(chat, "_get_client", lambda: _FakeClient())
    monkeypatch.setattr(chat, "dispatch", _raise_ch_error)

    out = await chat.chat_with_tools("質問", _ctx(), conn=None, agency_id=1, locale="ja")

    assert out["success"] is False
    assert _SECRET not in out["answer"]
    assert out["answer"] == chat._chat_str("service_unavailable", "ja", name="describe_data")


@pytest.mark.asyncio
async def test_generic_exception_no_longer_interpolates_exception_text(monkeypatch):
    """A generic/unexpected exception must not leak its message either — the
    tool_error string is now a fixed template with no {exc} placeholder."""

    async def _raise_generic(*a, **k):
        raise ValueError(_SECRET)

    monkeypatch.setattr(chat, "_get_client", lambda: _FakeClient())
    monkeypatch.setattr(chat, "dispatch", _raise_generic)

    out = await chat.chat_with_tools("質問", _ctx(), conn=None, agency_id=1, locale="ja")

    assert out["success"] is False
    assert _SECRET not in out["answer"]
    assert out["answer"] == chat._chat_str("tool_error", "ja", name="describe_data")


@pytest.mark.asyncio
async def test_english_locale_also_hides_exception_text(monkeypatch):
    """Same guarantee holds for the English locale strings."""

    async def _raise_ch_error(*a, **k):
        raise clickhouse_connect.driver.exceptions.DatabaseError(_SECRET)

    monkeypatch.setattr(chat, "_get_client", lambda: _FakeClient())
    monkeypatch.setattr(chat, "dispatch", _raise_ch_error)

    out = await chat.chat_with_tools("q", _ctx(), conn=None, agency_id=1, locale="en")

    assert _SECRET not in out["answer"]
    assert out["answer"] == chat._chat_str("service_unavailable", "en", name="describe_data")


@pytest.mark.asyncio
async def test_logger_still_captures_full_exception_detail(monkeypatch, caplog):
    """logger.exception must still record the real error server-side even
    though the user-facing text is now generic (both ClickHouse and generic
    failure paths)."""

    async def _raise_ch_error(*a, **k):
        raise clickhouse_connect.driver.exceptions.DatabaseError(_SECRET)

    monkeypatch.setattr(chat, "_get_client", lambda: _FakeClient())
    monkeypatch.setattr(chat, "dispatch", _raise_ch_error)

    with caplog.at_level(logging.ERROR, logger="pipeline.query.chat"):
        await chat.chat_with_tools("質問", _ctx(), conn=None, agency_id=1, locale="ja")

    assert any(rec.exc_info and _SECRET in str(rec.exc_info[1]) for rec in caplog.records)
