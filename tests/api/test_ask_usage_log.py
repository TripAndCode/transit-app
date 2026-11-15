"""Notebook card dispatches and follow-ups are logged to ``ask_query_log``:
the notebook's usage signal. One row per dispatch, whatever its outcome, and
a failed bookkeeping write never changes the response."""

from __future__ import annotations

import httpx
import pytest
from httpx import ASGITransport

from pipeline.query.followup import FollowupResult
from tests.api.test_conversations import (  # noqa: F401  (conv_app is a fixture)
    _CSRF,
    _authed_client,
    _authed_optional_client,
    conv_app,
)


async def _rows(pool) -> list:
    async with pool.acquire() as c:
        return await c.fetch(
            "SELECT agency_id, question, router_stage, tool, success, latency_ms, numeric_guard_triggered "
            "FROM ask_query_log ORDER BY id"
        )


@pytest.fixture
async def usage(conv_app, monkeypatch):  # noqa: F811
    import api.routers.conversations as conv_router
    from pipeline import flags
    from pipeline.query.results import ToolResult

    app, agency, uid, pool = conv_app
    monkeypatch.delenv("ASK_QUERY_LOG_ENABLED", raising=False)
    flags.invalidate()
    async with pool.acquire() as c:
        await c.execute("TRUNCATE ask_query_log")

    async def fake_dispatch(tool, args, ctx, conn, agency_id, locale="ja", ch=None):
        return ToolResult(kind="kv", summary="ok")

    monkeypatch.setattr(conv_router, "dispatch", fake_dispatch)
    yield app, agency, uid, pool
    async with pool.acquire() as c:
        await c.execute("TRUNCATE ask_query_log")
    flags.invalidate()


def _anon(app):
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _new_conversation(c, agency) -> str:
    r = await c.post(f"/api/{agency}/conversations", json={"title": "T", "filter_ctx": {}}, headers=_CSRF)
    return r.json()["conversation_id"]


async def _signed_in_card(app, agency, uid, tool="describe_data", args=None):
    async with _authed_client(app, uid) as c:
        conv_id = await _new_conversation(c, agency)
        r = await c.post(
            f"/api/{agency}/conversations/{conv_id}/messages",
            json={"tool": tool, "args": args or {}},
            headers=_CSRF,
        )
        thread = await c.get(f"/api/{agency}/conversations/{conv_id}/messages")
    return r, thread.json()


async def _signed_out_card(app, agency, tool="describe_data", args=None):
    async with _anon(app) as c:
        return await c.post(
            f"/api/{agency}/ask/card", json={"tool": tool, "args": args or {}, "filter_ctx": {}}, headers=_CSRF
        )


@pytest.mark.asyncio
async def test_signed_in_card_is_logged_under_its_canonical_tool(usage):
    app, agency, uid, pool = usage
    r, _ = await _signed_in_card(app, agency, uid, tool="on_time")
    assert r.status_code == 200, r.text
    [row] = await _rows(pool)
    assert row["router_stage"] == "card_signed_in"
    assert row["tool"] == "on_time_rate"
    assert row["success"] is True
    assert row["agency_id"] == agency
    assert row["question"] == ""
    assert row["latency_ms"] is not None and row["latency_ms"] >= 0


@pytest.mark.asyncio
async def test_signed_out_card_is_logged_under_its_own_stage(usage):
    app, agency, _uid, pool = usage
    r = await _signed_out_card(app, agency, tool="trend")
    assert r.status_code == 200, r.text
    [row] = await _rows(pool)
    assert row["router_stage"] == "card_signed_out"
    assert row["tool"] == "time_series"
    assert row["success"] is True
    assert row["question"] == ""
    assert row["latency_ms"] is not None and row["latency_ms"] >= 0


@pytest.mark.asyncio
async def test_empty_result_and_unknown_tool_log_as_failures_without_client_text(usage, monkeypatch):
    import api.routers.conversations as conv_router
    from pipeline.query.results import ToolResult

    app, agency, _uid, pool = usage

    async def empty_dispatch(tool, args, ctx, conn, agency_id, locale="ja", ch=None):
        return ToolResult(kind="empty", summary="nothing")

    monkeypatch.setattr(conv_router, "dispatch", empty_dispatch)
    await _signed_out_card(app, agency, tool="top_n")
    await _signed_out_card(app, agency, tool="<script>not-a-tool</script>")
    rows = await _rows(pool)
    assert [(r["tool"], r["success"]) for r in rows] == [("top_n", False), (None, False)]


@pytest.mark.asyncio
async def test_failed_dispatch_is_logged_for_both_paths(usage, monkeypatch):
    from fastapi import HTTPException

    import api.routers.conversations as conv_router

    app, agency, uid, pool = usage

    async def unavailable(*a, **k):
        raise HTTPException(status_code=503, detail="clickhouse")

    monkeypatch.setattr(conv_router, "dispatch", unavailable)
    await _signed_in_card(app, agency, uid)
    await _signed_out_card(app, agency)
    rows = await _rows(pool)
    assert [(r["router_stage"], r["tool"], r["success"]) for r in rows] == [
        ("card_signed_in", "describe_data", False),
        ("card_signed_out", "describe_data", False),
    ]


@pytest.mark.asyncio
async def test_a_propagating_dispatch_error_is_still_logged(usage, monkeypatch):
    import asyncpg

    import api.routers.conversations as conv_router

    app, agency, uid, pool = usage

    async def missing_table(*a, **k):
        raise asyncpg.exceptions.UndefinedTableError("agg_x")

    monkeypatch.setattr(conv_router, "dispatch", missing_table)
    async with _authed_client(app, uid) as c:
        conv_id = await _new_conversation(c, agency)
        r = await c.post(
            f"/api/{agency}/conversations/{conv_id}/messages",
            json={"tool": "describe_data", "args": {}},
            headers=_CSRF,
        )
    assert r.status_code == 503
    [row] = await _rows(pool)
    assert (row["router_stage"], row["success"]) == ("card_signed_in", False)


@pytest.fixture
async def broken_log(usage):
    """The log table rejects every insert: its INSERT names a column that no
    longer exists, so the failure is raised by Postgres, not by Python."""
    _app, _agency, _uid, pool = usage
    async with pool.acquire() as c:
        await c.execute("ALTER TABLE ask_query_log RENAME COLUMN latency_ms TO latency_ms_renamed")
    yield usage
    async with pool.acquire() as c:
        await c.execute("ALTER TABLE ask_query_log RENAME COLUMN latency_ms_renamed TO latency_ms")


@pytest.mark.asyncio
async def test_a_failing_log_write_does_not_break_a_signed_in_card(broken_log):
    app, agency, uid, _pool = broken_log
    r, thread = await _signed_in_card(app, agency, uid)
    assert r.status_code == 200, r.text
    assert [m["role"] for m in thread] == ["user", "assistant"]
    assert thread[1]["result"]["kind"] == "kv"


@pytest.mark.asyncio
async def test_a_failing_log_write_does_not_break_a_signed_out_card(broken_log):
    app, agency, _uid, _pool = broken_log
    r = await _signed_out_card(app, agency)
    assert r.status_code == 200, r.text
    assert r.json()["result"]["kind"] == "kv"


@pytest.mark.asyncio
async def test_the_kill_switch_stops_card_logging(usage, monkeypatch):
    from pipeline import flags

    app, agency, uid, pool = usage
    monkeypatch.setenv("ASK_QUERY_LOG_ENABLED", "false")
    flags.invalidate()
    await _signed_out_card(app, agency)
    await _signed_in_card(app, agency, uid)
    assert await _rows(pool) == []


async def _followup(app, agency, uid, pool, monkeypatch, result, *, fail_thread_write=False):
    import api.routers.conversations as conv_router

    monkeypatch.setenv("ASK_FOLLOWUP_ENABLED", "true")

    async def fake_answer_followup(*, question, context_tool, context_args, context_result, locale, llm_approved=True):
        return result

    monkeypatch.setattr(conv_router._followup, "answer_followup", fake_answer_followup)
    async with _authed_client(app, uid) as c:
        conv_id = await _new_conversation(c, agency)
    async with pool.acquire() as conn:
        msg = await conv_router._conv.append_message(
            conn,
            conv_id,
            role="assistant",
            chip_id=None,
            tool="describe_data",
            args={},
            signature_hash=None,
            result={"ok": True},
            rendered_summary="prior",
        )
    if fail_thread_write:

        async def failing_append(conn, conversation_id, **kw):
            raise RuntimeError("write failed")

        monkeypatch.setattr(conv_router._conv, "append_message", failing_append)
    async with _authed_optional_client(app, uid) as c:
        return await c.post(
            f"/api/{agency}/conversations/{conv_id}/followup",
            json={"question": "what about X?", "context_message_id": msg["message_id"]},
            headers=_CSRF,
        )


@pytest.mark.asyncio
async def test_followup_is_logged_with_its_context_tool(usage, monkeypatch):
    app, agency, uid, pool = usage
    r = await _followup(app, agency, uid, pool, monkeypatch, FollowupResult("the answer", None, False))
    assert r.status_code == 200, r.text
    [row] = await _rows(pool)
    assert (row["router_stage"], row["tool"], row["success"]) == ("followup", "describe_data", True)
    assert row["question"] == ""
    assert row["latency_ms"] is not None
    assert row["numeric_guard_triggered"] is False


@pytest.mark.asyncio
async def test_a_failed_followup_is_logged_as_a_failure(usage, monkeypatch):
    app, agency, uid, pool = usage
    r = await _followup(app, agency, uid, pool, monkeypatch, FollowupResult("", "provider_unavailable"))
    assert r.status_code == 502
    [row] = await _rows(pool)
    assert (row["router_stage"], row["success"], row["question"]) == ("followup", False, "")
    assert row["numeric_guard_triggered"] is None


@pytest.mark.asyncio
async def test_a_replaced_followup_answer_records_the_guard_verdict(usage, monkeypatch):
    app, agency, uid, pool = usage
    r = await _followup(app, agency, uid, pool, monkeypatch, FollowupResult("fallback", None, True))
    assert r.status_code == 200, r.text
    [row] = await _rows(pool)
    assert row["numeric_guard_triggered"] is True


@pytest.mark.asyncio
async def test_the_kill_switch_stops_followup_logging(usage, monkeypatch):
    from pipeline import flags

    app, agency, uid, pool = usage
    monkeypatch.setenv("ASK_QUERY_LOG_ENABLED", "false")
    flags.invalidate()
    await _followup(app, agency, uid, pool, monkeypatch, FollowupResult("", "provider_unavailable"))
    r = await _followup(app, agency, uid, pool, monkeypatch, FollowupResult("ok", None, False))
    assert r.status_code == 200
    assert await _rows(pool) == []


@pytest.mark.asyncio
async def test_card_latency_covers_the_dispatch_only(usage, monkeypatch):
    import asyncio

    import api.routers.conversations as conv_router
    from pipeline.query.results import ToolResult

    app, agency, uid, pool = usage

    async def slow_dispatch(tool, args, ctx, conn, agency_id, locale="ja", ch=None):
        await asyncio.sleep(0.2)
        return ToolResult(kind="kv", summary="ok")

    monkeypatch.setattr(conv_router, "dispatch", slow_dispatch)
    await _signed_in_card(app, agency, uid)
    await _signed_out_card(app, agency)
    rows = await _rows(pool)
    assert len(rows) == 2
    assert all(200 <= r["latency_ms"] < 1000 for r in rows)


@pytest.mark.asyncio
async def test_a_card_whose_thread_write_fails_is_logged_as_a_failure(usage, monkeypatch):
    import api.routers.conversations as conv_router

    app, agency, uid, pool = usage
    real_append = conv_router._conv.append_message

    async def failing_assistant_append(conn, conversation_id, *, role, **kw):
        if role == "assistant":
            raise RuntimeError("write failed")
        return await real_append(conn, conversation_id, role=role, **kw)

    monkeypatch.setattr(conv_router._conv, "append_message", failing_assistant_append)
    async with _authed_client(app, uid) as c:
        conv_id = await _new_conversation(c, agency)
        with pytest.raises(RuntimeError, match="write failed"):
            await c.post(
                f"/api/{agency}/conversations/{conv_id}/messages",
                json={"tool": "describe_data", "args": {}},
                headers=_CSRF,
            )
    [row] = await _rows(pool)
    assert (row["router_stage"], row["success"]) == ("card_signed_in", False)


@pytest.mark.asyncio
async def test_a_failed_user_message_write_logs_no_dispatch(usage, monkeypatch):
    import api.routers.conversations as conv_router

    app, agency, uid, pool = usage

    async def failing_append(conn, conversation_id, **kw):
        raise RuntimeError("write failed")

    monkeypatch.setattr(conv_router._conv, "append_message", failing_append)
    async with _authed_client(app, uid) as c:
        conv_id = await _new_conversation(c, agency)
        with pytest.raises(RuntimeError, match="write failed"):
            await c.post(
                f"/api/{agency}/conversations/{conv_id}/messages",
                json={"tool": "describe_data", "args": {}},
                headers=_CSRF,
            )
    assert await _rows(pool) == []


@pytest.mark.asyncio
async def test_a_followup_whose_thread_write_fails_is_logged_as_a_failure(usage, monkeypatch):
    app, agency, uid, pool = usage
    with pytest.raises(RuntimeError, match="write failed"):
        await _followup(
            app, agency, uid, pool, monkeypatch, FollowupResult("the answer", None, False), fail_thread_write=True
        )
    [row] = await _rows(pool)
    assert (row["router_stage"], row["success"]) == ("followup", False)


@pytest.mark.asyncio
@pytest.mark.parametrize(("error", "status"), [("empty", 400), ("too_long", 400)])
async def test_followup_input_errors_the_model_never_saw_are_not_logged(usage, monkeypatch, error, status):
    app, agency, uid, pool = usage
    r = await _followup(app, agency, uid, pool, monkeypatch, FollowupResult("", error))
    assert r.status_code == status
    assert await _rows(pool) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("result", "status"),
    [(FollowupResult("the answer", None, False), 200), (FollowupResult("", "provider_unavailable"), 502)],
)
async def test_a_failing_log_write_does_not_break_a_followup(broken_log, monkeypatch, result, status):
    app, agency, uid, pool = broken_log
    r = await _followup(app, agency, uid, pool, monkeypatch, result)
    assert r.status_code == status, r.text
    if status == 200:
        body = r.json()
        assert body["user"]["rendered_summary"] == "what about X?"
        assert body["assistant"]["rendered_summary"] == "the answer"
