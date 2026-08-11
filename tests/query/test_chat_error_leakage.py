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
import os
from datetime import date
from types import SimpleNamespace

import asyncpg
import clickhouse_connect
import pytest
from fastapi import HTTPException

from api.range import RangeCtx
from pipeline.query import chat

DATABASE_URL = os.environ["DATABASE_URL"]

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


@pytest.mark.asyncio
async def test_undefined_table_error_propagates_flag_off_path(monkeypatch):
    """A missing agg_* table (migration/analyze behind) must propagate out of
    chat_with_tools untouched — not be swallowed into a generic 200 tool_error
    — so a caller's registered aggregate_not_ready_handler can turn it into
    the machine-readable 503 the frontend reacts to (Fix-9i regression:
    review found the generic ``except Exception`` clause was catching this
    before it could reach that handler)."""

    async def _raise_undefined_table(*a, **k):
        raise asyncpg.exceptions.UndefinedTableError('relation "agg_route_daily_dist" does not exist')

    monkeypatch.setattr(chat, "_get_client", lambda: _FakeClient())
    monkeypatch.setattr(chat, "dispatch", _raise_undefined_table)

    with pytest.raises(asyncpg.exceptions.UndefinedTableError):
        await chat.chat_with_tools("質問", _ctx(), conn=None, agency_id=1, locale="ja")


# ─── Build-mode sentinel site (Site 1: `__build__ TOOL {json}`) ──────────────
#
# This is the zero-LLM determinism path the guided builder UI submits — live
# in production, not gated behind any flag. Fix-9i's review found the initial
# leakage-test suite only exercised the Stage-2/flag-off site; these pin the
# same guarantees for build-mode's own try/except around dispatch(...).


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raiser,expected_key",
    [
        (lambda: HTTPException(status_code=503, detail=_SECRET), "service_unavailable"),
        (lambda: clickhouse_connect.driver.exceptions.DatabaseError(_SECRET), "service_unavailable"),
        (lambda: ValueError(_SECRET), "tool_error"),
    ],
    ids=["http_503", "clickhouse_error", "generic_exception"],
)
async def test_build_mode_sentinel_hides_exception_text(monkeypatch, raiser, expected_key):
    """The build-mode dispatch except-block must degrade to the safe locale
    string, never the raw exception text, for all three failure classes."""

    async def _raise(*a, **k):
        raise raiser()

    monkeypatch.setattr(chat, "_get_client", lambda: _FakeClient())
    monkeypatch.setattr(chat, "dispatch", _raise)

    out = await chat.chat_with_tools("__build__ describe_data {}", _ctx(), conn=None, agency_id=1, locale="ja")

    assert out["success"] is False
    assert _SECRET not in out["answer"]
    assert out["answer"] == chat._chat_str(expected_key, "ja", name="describe_data")
    assert out["cache_outcome"] == "bypass"


@pytest.mark.asyncio
async def test_build_mode_sentinel_non_503_http_exception_propagates(monkeypatch):
    """A non-503 HTTPException from build-mode dispatch is a real error and
    must propagate, matching the other four sites' behavior."""

    async def _raise_400(*a, **k):
        raise HTTPException(status_code=400, detail="bad request")

    monkeypatch.setattr(chat, "_get_client", lambda: _FakeClient())
    monkeypatch.setattr(chat, "dispatch", _raise_400)

    with pytest.raises(HTTPException) as exc_info:
        await chat.chat_with_tools("__build__ describe_data {}", _ctx(), conn=None, agency_id=1, locale="ja")
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_build_mode_sentinel_undefined_table_error_propagates(monkeypatch):
    """A missing agg_* table during build-mode dispatch must also propagate,
    not be swallowed — same guarantee as the other four sites."""

    async def _raise_undefined_table(*a, **k):
        raise asyncpg.exceptions.UndefinedTableError('relation "agg_route_daily_dist" does not exist')

    monkeypatch.setattr(chat, "_get_client", lambda: _FakeClient())
    monkeypatch.setattr(chat, "dispatch", _raise_undefined_table)

    with pytest.raises(asyncpg.exceptions.UndefinedTableError):
        await chat.chat_with_tools("__build__ describe_data {}", _ctx(), conn=None, agency_id=1, locale="ja")


# ─── Cache pre-hit site (Site 2: ASK_INTENT_CACHE_ENABLED=true, exact-question
# pre-LLM lookup hit) ──────────────────────────────────────────────────────────
#
# Requires a real DB: the pre-hit path reads/writes ask_intent_cache. Mirrors
# tests/query/test_chat_intent_cache.py's pool_with_agency fixture convention.


@pytest.fixture
async def pool_with_agency(apply_schema):
    """Pool + agency_id + a route so describe_data + tool dispatch can run."""
    pool = await asyncpg.create_pool(DATABASE_URL)
    async with pool.acquire() as c:
        await c.execute("DELETE FROM ask_intent_cache")
        row = await c.fetchrow(
            "INSERT INTO agencies (agency_name, feed_url) VALUES ('T', 'http://t') RETURNING agency_id"
        )
        agency_id = row["agency_id"]
    yield pool, agency_id
    async with pool.acquire() as c:
        await c.execute("DELETE FROM ask_intent_cache")
        await c.execute("TRUNCATE agencies CASCADE")
    await pool.close()


async def _seed_cache_row(pool, agency_id: int, question: str, tool: str = "describe_data"):
    """Pre-populate ask_intent_cache so a later chat_with_tools call for the
    same question text hits the pre-LLM lookup (Site 2), not the LLM path."""
    from pipeline.query.intent import IntentSignature, canonicalize, signature_hash

    ctx_dict = {"from_date": _ctx().from_date, "to_date": _ctx().to_date}
    can_args = canonicalize(tool, {}, ctx_dict)
    sig_hash = signature_hash(tool, can_args)
    sig = IntentSignature(tool=tool, args={}, confidence=0.9)

    from pipeline.query import intent_cache as ic

    async with pool.acquire() as conn:
        await ic.upsert(conn, sig_hash, sig, can_args, agency_id, question=question)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raiser,expected_key",
    [
        (lambda: HTTPException(status_code=503, detail=_SECRET), "service_unavailable"),
        (lambda: clickhouse_connect.driver.exceptions.DatabaseError(_SECRET), "service_unavailable"),
        (lambda: ValueError(_SECRET), "tool_error"),
    ],
    ids=["http_503", "clickhouse_error", "generic_exception"],
)
async def test_cache_pre_hit_hides_exception_text(monkeypatch, pool_with_agency, raiser, expected_key):
    """The cache-pre-hit dispatch except-block (ASK_INTENT_CACHE_ENABLED=true,
    exact-question-text lookup hit — skips the LLM entirely) must degrade to
    the safe locale string, never the raw exception text."""
    pool, agency_id = pool_with_agency
    monkeypatch.setenv("ASK_INTENT_CACHE_ENABLED", "true")
    question = "いつからのデータ？"
    await _seed_cache_row(pool, agency_id, question)

    async def _raise(*a, **k):
        raise raiser()

    monkeypatch.setattr(chat, "_get_client", lambda: _FakeClient())
    monkeypatch.setattr(chat, "dispatch", _raise)

    async with pool.acquire() as conn:
        out = await chat.chat_with_tools(question, _ctx(), conn, agency_id, locale="ja")

    assert out["success"] is False
    assert _SECRET not in out["answer"]
    assert out["answer"] == chat._chat_str(expected_key, "ja", name="describe_data")
    assert out["cache_outcome"] == "hit"


@pytest.mark.asyncio
async def test_cache_pre_hit_undefined_table_error_propagates(monkeypatch, pool_with_agency):
    """A missing agg_* table during a cache-pre-hit dispatch must also
    propagate, not be swallowed — same guarantee as the other four sites."""
    pool, agency_id = pool_with_agency
    monkeypatch.setenv("ASK_INTENT_CACHE_ENABLED", "true")
    question = "いつからのデータ？"
    await _seed_cache_row(pool, agency_id, question)

    async def _raise_undefined_table(*a, **k):
        raise asyncpg.exceptions.UndefinedTableError('relation "agg_route_daily_dist" does not exist')

    monkeypatch.setattr(chat, "_get_client", lambda: _FakeClient())
    monkeypatch.setattr(chat, "dispatch", _raise_undefined_table)

    async with pool.acquire() as conn:
        with pytest.raises(asyncpg.exceptions.UndefinedTableError):
            await chat.chat_with_tools(question, _ctx(), conn, agency_id, locale="ja")
