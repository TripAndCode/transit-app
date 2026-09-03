"""HTTP-layer tests for /api/{agency_id}/conversations/*."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone

import asyncpg
import httpx
import pytest
from httpx import ASGITransport

from tests.conftest import TEST_ORIGIN

DATABASE_URL = os.environ["DATABASE_URL"]
_CSRF = {"Origin": TEST_ORIGIN}


@pytest.fixture
async def conv_app(apply_schema):
    """Agency + a user; client authed as that user."""
    from api.main import app

    pool = await asyncpg.create_pool(DATABASE_URL)
    app.state.pool = pool
    # append_message_endpoint now declares ch=Depends(get_ch) alongside conn
    # (Task 8); tests in this file mock dispatch so the real client is never
    # touched, but FastAPI still resolves the dependency — None is fine here.
    app.state.ch_client = None

    async with pool.acquire() as c:
        await c.execute("DELETE FROM ask_conversations")
        a = await c.fetchrow(
            "INSERT INTO agencies (agency_name, feed_url) VALUES ('T', 'http://t') RETURNING agency_id"
        )
        u = await c.fetchrow("INSERT INTO users (email, name, role) VALUES ('t@test', 'T', 'user') RETURNING user_id")
    yield app, a["agency_id"], u["user_id"], pool
    async with pool.acquire() as c:
        await c.execute("DELETE FROM ask_conversations")
        await c.execute("DELETE FROM users WHERE email IN ('t@test', 'other@test')")
        await c.execute("TRUNCATE agencies CASCADE")
    await pool.close()


@asynccontextmanager
async def _authed_client(app, user_id: int):
    """Build an httpx async client with an override that injects the user.

    Uses a context manager so the dependency override is always cleaned up,
    preventing state leakage into subsequent tests.
    """
    from api.deps import get_current_user
    from api.security import User

    fake_user = User(
        user_id=user_id,
        email="t@test",
        name="T",
        avatar_url=None,
        role="user",
        suspended_at=None,
    )
    app.dependency_overrides[get_current_user] = lambda: fake_user
    try:
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@asynccontextmanager
async def _authed_optional_client(app, user_id: int):
    """Like ``_authed_client``, but overrides ``get_current_user_optional``
    instead — used by endpoints (e.g. ``followup_endpoint``) that accept both
    authenticated and anonymous callers, so ``get_current_user`` is never
    resolved for them."""
    from api.deps import get_current_user_optional
    from api.security import User

    fake_user = User(
        user_id=user_id,
        email="t@test",
        name="T",
        avatar_url=None,
        role="user",
        suspended_at=None,
    )
    app.dependency_overrides[get_current_user_optional] = lambda: fake_user
    try:
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_current_user_optional, None)


@pytest.mark.asyncio
async def test_create_and_list_conversation(conv_app):
    app, agency, uid, _ = conv_app
    async with _authed_client(app, uid) as c:
        r = await c.post(
            f"/api/{agency}/conversations", json={"title": "First", "filter_ctx": {"dow": "weekday"}}, headers=_CSRF
        )
        assert r.status_code == 200, r.text
        conv_id = r.json()["conversation_id"]
        r2 = await c.get(f"/api/{agency}/conversations")
        assert r2.status_code == 200
        assert any(cv["conversation_id"] == conv_id for cv in r2.json())


@pytest.mark.asyncio
async def test_get_others_conversation_is_404(conv_app):
    app, agency, uid, pool = conv_app
    # Create with uid; then create a second user and try to access.
    async with _authed_client(app, uid) as c:
        r = await c.post(f"/api/{agency}/conversations", json={"title": "X", "filter_ctx": {}}, headers=_CSRF)
        conv_id = r.json()["conversation_id"]
    async with pool.acquire() as conn:
        other = await conn.fetchrow(
            "INSERT INTO users (email, name, role) VALUES ('other@test', 'O', 'user') RETURNING user_id"
        )
    async with _authed_client(app, other["user_id"]) as c:
        r = await c.get(f"/api/{agency}/conversations/{conv_id}")
        assert r.status_code == 404  # masquerade as not-found for non-owners


@pytest.mark.asyncio
async def test_others_conversation_patch_delete_messages_are_404(conv_app):
    """Characterization test for `_owned_or_404`'s shared ownership-mask-as-404
    behavior across update/delete/list_messages/append_message."""
    app, agency, uid, pool = conv_app
    async with _authed_client(app, uid) as c:
        r = await c.post(f"/api/{agency}/conversations", json={"title": "X", "filter_ctx": {}}, headers=_CSRF)
        conv_id = r.json()["conversation_id"]
    async with pool.acquire() as conn:
        other = await conn.fetchrow(
            "INSERT INTO users (email, name, role) VALUES ('other@test', 'O', 'user') RETURNING user_id"
        )
    async with _authed_client(app, other["user_id"]) as c:
        r_patch = await c.patch(f"/api/{agency}/conversations/{conv_id}", json={"title": "Hijacked"}, headers=_CSRF)
        assert r_patch.status_code == 404
        assert r_patch.json()["detail"] == "not found"

        r_messages = await c.get(f"/api/{agency}/conversations/{conv_id}/messages")
        assert r_messages.status_code == 404
        assert r_messages.json()["detail"] == "not found"

        r_append = await c.post(
            f"/api/{agency}/conversations/{conv_id}/messages",
            json={"tool": "route_stats", "args": {"route": "1"}},
            headers=_CSRF,
        )
        assert r_append.status_code == 404
        assert r_append.json()["detail"] == "not found"

        r_delete = await c.delete(f"/api/{agency}/conversations/{conv_id}", headers=_CSRF)
        assert r_delete.status_code == 404
        assert r_delete.json()["detail"] == "not found"


@pytest.mark.asyncio
async def test_get_own_conversation_under_wrong_agency_path_is_404(conv_app):
    """A conversation owned by the caller but created under a different
    agency must 404 when accessed via another agency's URL path - the
    ownership check must verify agency_id, not just user_id."""
    app, agency, uid, pool = conv_app
    async with pool.acquire() as conn:
        other_agency = await conn.fetchrow(
            "INSERT INTO agencies (agency_name, feed_url) VALUES ('Other', 'http://other') RETURNING agency_id"
        )
    async with _authed_client(app, uid) as c:
        r = await c.post(f"/api/{agency}/conversations", json={"title": "X", "filter_ctx": {}}, headers=_CSRF)
        conv_id = r.json()["conversation_id"]
        r2 = await c.get(f"/api/{other_agency['agency_id']}/conversations/{conv_id}")
        assert r2.status_code == 404


@pytest.mark.asyncio
async def test_patch_and_delete(conv_app):
    app, agency, uid, _ = conv_app
    async with _authed_client(app, uid) as c:
        r = await c.post(f"/api/{agency}/conversations", json={"title": "X", "filter_ctx": {}}, headers=_CSRF)
        conv_id = r.json()["conversation_id"]
        r2 = await c.patch(
            f"/api/{agency}/conversations/{conv_id}", json={"title": "Renamed", "pinned": True}, headers=_CSRF
        )
        assert r2.status_code == 200, r2.text
        assert r2.json()["title"] == "Renamed"
        assert r2.json()["pinned"] is True
        r3 = await c.delete(f"/api/{agency}/conversations/{conv_id}", headers=_CSRF)
        assert r3.status_code == 200


@pytest.mark.asyncio
async def test_append_message_chip_id_returns_410(conv_app):
    """chip_id dispatch was removed in Phase ③.5; endpoint now returns 410 Gone."""
    app, agency, uid, _ = conv_app
    async with _authed_client(app, uid) as c:
        cr = await c.post(f"/api/{agency}/conversations", json={"title": "X", "filter_ctx": {}}, headers=_CSRF)
        conv_id = cr.json()["conversation_id"]
        r = await c.post(
            f"/api/{agency}/conversations/{conv_id}/messages", json={"chip_id": "meta-routes"}, headers=_CSRF
        )
        assert r.status_code == 410
        assert "chip dispatch" in r.json()["detail"]


@pytest.mark.asyncio
async def test_append_message_unknown_chip_id_returns_410(conv_app):
    """Any chip_id (including unknown ones) now returns 410 Gone."""
    app, agency, uid, _ = conv_app
    async with _authed_client(app, uid) as c:
        cr = await c.post(f"/api/{agency}/conversations", json={"title": "X", "filter_ctx": {}}, headers=_CSRF)
        conv_id = cr.json()["conversation_id"]
        r = await c.post(
            f"/api/{agency}/conversations/{conv_id}/messages", json={"chip_id": "does-not-exist"}, headers=_CSRF
        )
        assert r.status_code == 410


@pytest.mark.asyncio
async def test_append_message_tool_args_path(conv_app):
    """Builder direct-dispatch path: {tool, args} without chip_id appends messages."""
    app, agency, uid, pool = conv_app
    # Seed a route so describe_data tool has something to work with.
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO static_routes (agency_id, route_id, route_short_name) VALUES ($1, 'R1', 'R1')",
            agency,
        )
    async with _authed_client(app, uid) as c:
        cr = await c.post(
            f"/api/{agency}/conversations", json={"title": "Builder test", "filter_ctx": {}}, headers=_CSRF
        )
        conv_id = cr.json()["conversation_id"]
        r = await c.post(
            f"/api/{agency}/conversations/{conv_id}/messages",
            json={"tool": "describe_data", "args": {}},
            headers=_CSRF,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        # chip_id should be null in both messages for the builder path
        assert body["user"]["chip_id"] is None
        assert body["assistant"]["tool"] == "describe_data"
        # Two messages appended
        ml = await c.get(f"/api/{agency}/conversations/{conv_id}/messages")
        assert ml.status_code == 200
        assert len(ml.json()) == 2


@pytest.mark.asyncio
async def test_append_message_default_window_uses_jst_today(conv_app, monkeypatch):
    """When a conversation's filter_ctx has no explicit dates, the default
    30-day window built for tool dispatch must anchor on the JST civil
    calendar (jst_today()), not the server's local/UTC date - the same
    class of bug fixed elsewhere via api.range.jst_today()."""
    import api.range as range_mod
    import api.routers.conversations as conv_router

    app, agency, uid, _pool = conv_app

    fixed_utc = datetime(2026, 1, 1, 20, 0, tzinfo=timezone.utc)

    class FakeDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_utc.astimezone(tz) if tz else fixed_utc

    monkeypatch.setattr(range_mod, "datetime", FakeDateTime)

    captured = {}

    async def _fake_dispatch(tool, args, ctx, conn, agency_id, locale="ja", ch=None):
        captured["ctx"] = ctx
        from pipeline.query.results import ToolResult

        return ToolResult(kind="kv", summary="ok")

    monkeypatch.setattr(conv_router, "dispatch", _fake_dispatch)

    async with _authed_client(app, uid) as c:
        cr = await c.post(f"/api/{agency}/conversations", json={"title": "T", "filter_ctx": {}}, headers=_CSRF)
        conv_id = cr.json()["conversation_id"]
        r = await c.post(
            f"/api/{agency}/conversations/{conv_id}/messages",
            json={"tool": "describe_data", "args": {}},
            headers=_CSRF,
        )
        assert r.status_code == 200, r.text

    assert captured["ctx"].to_date == date(2026, 1, 2)


@pytest.mark.asyncio
async def test_append_message_both_chip_and_tool_400(conv_app):
    """Providing both chip_id and tool+args is a 400."""
    app, agency, uid, _ = conv_app
    async with _authed_client(app, uid) as c:
        cr = await c.post(f"/api/{agency}/conversations", json={"title": "X", "filter_ctx": {}}, headers=_CSRF)
        conv_id = cr.json()["conversation_id"]
        r = await c.post(
            f"/api/{agency}/conversations/{conv_id}/messages",
            json={"chip_id": "meta-routes", "tool": "describe_data", "args": {}},
            headers=_CSRF,
        )
        assert r.status_code == 400


@pytest.mark.asyncio
async def test_append_message_neither_chip_nor_tool_400(conv_app):
    """Providing neither chip_id nor tool+args is a 400."""
    app, agency, uid, _ = conv_app
    async with _authed_client(app, uid) as c:
        cr = await c.post(f"/api/{agency}/conversations", json={"title": "X", "filter_ctx": {}}, headers=_CSRF)
        conv_id = cr.json()["conversation_id"]
        r = await c.post(
            f"/api/{agency}/conversations/{conv_id}/messages",
            json={},
            headers=_CSRF,
        )
        assert r.status_code == 400


@pytest.mark.asyncio
async def test_migrate_anon_idempotent(conv_app):
    app, agency, uid, _ = conv_app
    payload = {
        "threads": [
            {
                "client_id": "anon-1",
                "title": "Anon A",
                "filter_ctx": {},
                "pinned": False,
                "created_at": "2026-05-29T10:00:00",
                "updated_at": "2026-05-29T10:00:00",
                "messages": [],
            },
            {
                "client_id": "anon-2",
                "title": "Anon B",
                "filter_ctx": {"dow": "weekday"},
                "pinned": True,
                "created_at": "2026-05-29T11:00:00",
                "updated_at": "2026-05-29T11:00:00",
                "messages": [],
            },
        ]
    }
    async with _authed_client(app, uid) as c:
        r1 = await c.post(f"/api/{agency}/conversations/migrate-anon", json=payload, headers=_CSRF)
        assert r1.status_code == 200, r1.text
        assert r1.json()["inserted"] == 2
        r2 = await c.post(f"/api/{agency}/conversations/migrate-anon", json=payload, headers=_CSRF)
        assert r2.json()["inserted"] == 0
        r3 = await c.get(f"/api/{agency}/conversations")
        assert len(r3.json()) == 2


# ─── Error-leakage regression (Fix-9i) ─────────────────────────────────────
#
# A dispatch() failure during append_message_endpoint used to render
# f"ツール {tool} の実行に失敗しました: {exc}" straight into rendered_summary,
# which is PERSISTED to ask_conversation_messages — so a leaked ClickHouse
# error string (SQL fragment / server version / query endpoint URL) would
# resurface every time the conversation is reloaded, not just once. These
# tests pin: (a) the ClickHouse-unavailable and (b) generic-exception paths
# both degrade to the safe locale strings, never the raw exception text,
# in BOTH the HTTP response and the row actually written to the DB; (c) a
# non-503 HTTPException still propagates untouched; (d) the real error is
# still logged server-side.

_SECRET = "http://ch-internal.example:8123/?database=transit&param_x=leak SELECT * FROM updates"


@pytest.mark.asyncio
async def test_append_message_clickhouse_unavailable_hides_exception_text(conv_app, monkeypatch):
    """dispatch() raising the _ClickHouseUnavailable stand-in's HTTPException(503)
    must persist+return the generic service_unavailable text, never the raw detail."""
    from fastapi import HTTPException

    import api.routers.conversations as conv_router
    from pipeline.query.chat import _chat_str

    app, agency, uid, pool = conv_app

    async def _raise_503(tool, args, ctx, conn, agency_id, locale="ja", ch=None):
        raise HTTPException(status_code=503, detail=_SECRET)

    monkeypatch.setattr(conv_router, "dispatch", _raise_503)

    async with _authed_client(app, uid) as c:
        cr = await c.post(f"/api/{agency}/conversations", json={"title": "T", "filter_ctx": {}}, headers=_CSRF)
        conv_id = cr.json()["conversation_id"]
        r = await c.post(
            f"/api/{agency}/conversations/{conv_id}/messages",
            json={"tool": "describe_data", "args": {}},
            headers=_CSRF,
        )
        assert r.status_code == 200, r.text
        rendered = r.json()["assistant"]["rendered_summary"]
        assert _SECRET not in rendered
        assert rendered == _chat_str("service_unavailable", "ja", name="describe_data")

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT rendered_summary FROM ask_conversation_messages WHERE conversation_id = $1 AND role = 'assistant'",
            conv_id,
        )
    assert row is not None
    assert _SECRET not in row["rendered_summary"]
    assert row["rendered_summary"] == rendered


@pytest.mark.asyncio
async def test_append_message_clickhouse_query_error_hides_exception_text(conv_app, monkeypatch):
    """A mid-query clickhouse_connect error (real client, query failed) gets
    the same graceful degrade as the client-unavailable stand-in."""
    import clickhouse_connect

    import api.routers.conversations as conv_router
    from pipeline.query.chat import _chat_str

    app, agency, uid, pool = conv_app

    async def _raise_ch_error(tool, args, ctx, conn, agency_id, locale="ja", ch=None):
        raise clickhouse_connect.driver.exceptions.DatabaseError(_SECRET)

    monkeypatch.setattr(conv_router, "dispatch", _raise_ch_error)

    async with _authed_client(app, uid) as c:
        cr = await c.post(f"/api/{agency}/conversations", json={"title": "T", "filter_ctx": {}}, headers=_CSRF)
        conv_id = cr.json()["conversation_id"]
        r = await c.post(
            f"/api/{agency}/conversations/{conv_id}/messages",
            json={"tool": "describe_data", "args": {}},
            headers=_CSRF,
        )
        assert r.status_code == 200, r.text
        rendered = r.json()["assistant"]["rendered_summary"]
        assert _SECRET not in rendered
        assert rendered == _chat_str("service_unavailable", "ja", name="describe_data")

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT rendered_summary FROM ask_conversation_messages WHERE conversation_id = $1 AND role = 'assistant'",
            conv_id,
        )
    assert row is not None
    assert _SECRET not in row["rendered_summary"]


@pytest.mark.asyncio
async def test_append_message_non_503_http_exception_propagates(conv_app, monkeypatch):
    """A non-503 HTTPException from dispatch (a real 4xx tool error) must
    propagate untouched, not be swallowed into a graceful 200 answer."""
    from fastapi import HTTPException

    import api.routers.conversations as conv_router

    app, agency, uid, _pool = conv_app

    async def _raise_400(tool, args, ctx, conn, agency_id, locale="ja", ch=None):
        raise HTTPException(status_code=400, detail="bad tool args")

    monkeypatch.setattr(conv_router, "dispatch", _raise_400)

    async with _authed_client(app, uid) as c:
        cr = await c.post(f"/api/{agency}/conversations", json={"title": "T", "filter_ctx": {}}, headers=_CSRF)
        conv_id = cr.json()["conversation_id"]
        r = await c.post(
            f"/api/{agency}/conversations/{conv_id}/messages",
            json={"tool": "describe_data", "args": {}},
            headers=_CSRF,
        )
        assert r.status_code == 400


@pytest.mark.asyncio
async def test_append_message_generic_exception_does_not_leak_text(conv_app, monkeypatch):
    """A genuinely unexpected exception (not ClickHouse, not HTTPException)
    must still degrade gracefully without echoing its raw message into the
    persisted rendered_summary."""
    import api.routers.conversations as conv_router
    from pipeline.query.chat import _chat_str

    app, agency, uid, pool = conv_app

    async def _raise_generic(tool, args, ctx, conn, agency_id, locale="ja", ch=None):
        raise ValueError(_SECRET)

    monkeypatch.setattr(conv_router, "dispatch", _raise_generic)

    async with _authed_client(app, uid) as c:
        cr = await c.post(f"/api/{agency}/conversations", json={"title": "T", "filter_ctx": {}}, headers=_CSRF)
        conv_id = cr.json()["conversation_id"]
        r = await c.post(
            f"/api/{agency}/conversations/{conv_id}/messages",
            json={"tool": "describe_data", "args": {}},
            headers=_CSRF,
        )
        assert r.status_code == 200, r.text
        rendered = r.json()["assistant"]["rendered_summary"]
        assert _SECRET not in rendered
        assert rendered == _chat_str("tool_error", "ja", name="describe_data")

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT rendered_summary FROM ask_conversation_messages WHERE conversation_id = $1 AND role = 'assistant'",
            conv_id,
        )
    assert row is not None
    assert _SECRET not in row["rendered_summary"]


@pytest.mark.asyncio
async def test_append_message_logs_full_exception_server_side(conv_app, monkeypatch, caplog):
    """Even though the user-facing/persisted text is now generic, the real
    error must still be captured server-side via logger.exception."""
    import logging

    import api.routers.conversations as conv_router

    app, agency, uid, _pool = conv_app

    async def _raise_generic(tool, args, ctx, conn, agency_id, locale="ja", ch=None):
        raise ValueError(_SECRET)

    monkeypatch.setattr(conv_router, "dispatch", _raise_generic)

    async with _authed_client(app, uid) as c:
        cr = await c.post(f"/api/{agency}/conversations", json={"title": "T", "filter_ctx": {}}, headers=_CSRF)
        conv_id = cr.json()["conversation_id"]
        with caplog.at_level(logging.ERROR, logger="api.routers.conversations"):
            r = await c.post(
                f"/api/{agency}/conversations/{conv_id}/messages",
                json={"tool": "describe_data", "args": {}},
                headers=_CSRF,
            )
        assert r.status_code == 200, r.text

    assert any(rec.exc_info and _SECRET in str(rec.exc_info[1]) for rec in caplog.records)


@pytest.mark.asyncio
async def test_append_message_undefined_table_error_propagates(conv_app, monkeypatch):
    """Fix-9i regression: dispatch() raising asyncpg.exceptions.UndefinedTableError
    (an agg_* table missing on a migration-lagged environment) must propagate
    out of append_message_endpoint so FastAPI's registered
    aggregate_not_ready_handler (api/main.py + api/aggregate_errors.py) turns
    it into the machine-readable {"code": "aggregate_not_ready"} 503 the
    frontend reacts to — mirroring api/routers/ask.py's Fix-8f.

    Before this fix, the blanket `except Exception` swallowed this into a
    generic 200 tool_error. Worse, dispatch() runs inside this endpoint's
    `async with conn.transaction():`, so if the except block had gone on to
    run another query on the same (now-aborted) connection — as the
    'except Exception' branch here does, via _conv.append_message(conn, ...)
    — Postgres would raise asyncpg.exceptions.InFailedSQLTransactionError
    instead, surfacing as an unhandled bare 500. This test pins that neither
    happens: the response is the clean 503 aggregate_not_ready shape."""
    import api.routers.conversations as conv_router

    app, agency, uid, _pool = conv_app

    async def _raise_undefined_table(tool, args, ctx, conn, agency_id, locale="ja", ch=None):
        raise asyncpg.exceptions.UndefinedTableError('relation "agg_route_daily_dist" does not exist')

    monkeypatch.setattr(conv_router, "dispatch", _raise_undefined_table)

    async with _authed_client(app, uid) as c:
        cr = await c.post(f"/api/{agency}/conversations", json={"title": "T", "filter_ctx": {}}, headers=_CSRF)
        conv_id = cr.json()["conversation_id"]
        r = await c.post(
            f"/api/{agency}/conversations/{conv_id}/messages",
            json={"tool": "describe_data", "args": {}},
            headers=_CSRF,
        )
        assert r.status_code == 503, f"expected 503 aggregate_not_ready, got {r.status_code}: {r.text[:200]}"
        data = r.json()
        assert data["code"] == "aggregate_not_ready"
        # The internal relation name is server-log-only, never client-visible.
        assert "agg_route_daily_dist" not in data["detail"]

        # And the conversation itself is left usable — no half-written user
        # message with no matching assistant reply, no poisoned connection
        # leaking into the next request on this same conversation.
        ml = await c.get(f"/api/{agency}/conversations/{conv_id}/messages")
        assert ml.status_code == 200
        assert ml.json() == []


@pytest.mark.asyncio
async def test_append_message_postgres_error_in_dispatch_does_not_poison_transaction(conv_app, monkeypatch):
    """A Postgres error raised by dispatch() OTHER than UndefinedTableError
    (e.g. a real statement failure/timeout) must still degrade to the
    generic tool_error message, not crash.

    Unlike the mocked-exception tests above, this dispatch stub runs a real
    failing statement on `conn` — the same connection append_message_endpoint
    holds inside its `async with conn.transaction():` — so the connection's
    transaction genuinely aborts, reproducing what a real asyncpg.PostgresError
    (anything but UndefinedTableError) does. Before the fix, the generic
    `except Exception` branch went on to call `_conv.append_message(conn, ...)`
    on that same aborted connection, which raises
    asyncpg.exceptions.InFailedSQLTransactionError — an unhandled 500 instead
    of the intended graceful tool_error response. The fix nests dispatch in
    its own SAVEPOINT (conn.transaction() called again while already inside
    one), confining the abort so the outer transaction stays writable."""
    import api.routers.conversations as conv_router

    app, agency, uid, pool = conv_app

    async def _raise_after_aborting_conn(tool, args, ctx, conn, agency_id, locale="ja", ch=None):
        await conn.execute("SELECT 1/0")

    monkeypatch.setattr(conv_router, "dispatch", _raise_after_aborting_conn)

    async with _authed_client(app, uid) as c:
        cr = await c.post(f"/api/{agency}/conversations", json={"title": "T", "filter_ctx": {}}, headers=_CSRF)
        conv_id = cr.json()["conversation_id"]
        r = await c.post(
            f"/api/{agency}/conversations/{conv_id}/messages",
            json={"tool": "describe_data", "args": {}},
            headers=_CSRF,
        )
        assert r.status_code == 200, r.text

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT role, rendered_summary FROM ask_conversation_messages "
            "WHERE conversation_id = $1 ORDER BY created_at",
            conv_id,
        )
    assert [r["role"] for r in rows] == ["user", "assistant"]


# ─── followup_endpoint (kill-switch gated LLM follow-up) ──────────────────────
#
# Characterization tests pinning followup_endpoint's ownership check
# (shared via _owned_or_404()) and its too_long/llm_error mapping. The LLM
# client is never called live — pipeline.query.followup.answer_followup is
# mocked.


@pytest.mark.asyncio
async def test_followup_disabled_by_default_returns_503(conv_app, monkeypatch):
    monkeypatch.delenv("ASK_FOLLOWUP_ENABLED", raising=False)
    app, agency, uid, _pool = conv_app
    async with _authed_client(app, uid) as c:
        cr = await c.post(f"/api/{agency}/conversations", json={"title": "T", "filter_ctx": {}}, headers=_CSRF)
        conv_id = cr.json()["conversation_id"]
    async with _authed_optional_client(app, uid) as c:
        r = await c.post(
            f"/api/{agency}/conversations/{conv_id}/followup",
            json={"question": "q", "context_message_id": 1},
            headers=_CSRF,
        )
    assert r.status_code == 503
    assert r.json()["detail"] == "followup_disabled"


@pytest.mark.asyncio
async def test_followup_others_conversation_is_404(conv_app, monkeypatch):
    """Authed path: accessing another user's conversation masquerades as 404,
    same as the CRUD endpoints (via _owned_or_404)."""
    monkeypatch.setenv("ASK_FOLLOWUP_ENABLED", "true")
    app, agency, uid, pool = conv_app
    async with _authed_client(app, uid) as c:
        cr = await c.post(f"/api/{agency}/conversations", json={"title": "X", "filter_ctx": {}}, headers=_CSRF)
        conv_id = cr.json()["conversation_id"]
    async with pool.acquire() as conn:
        other = await conn.fetchrow(
            "INSERT INTO users (email, name, role) VALUES ('other@test', 'O', 'user') RETURNING user_id"
        )
    async with _authed_optional_client(app, other["user_id"]) as c:
        r = await c.post(
            f"/api/{agency}/conversations/{conv_id}/followup",
            json={"question": "q", "context_message_id": 1},
            headers=_CSRF,
        )
    assert r.status_code == 404
    assert r.json()["detail"] == "not found"


@pytest.mark.asyncio
async def test_followup_authed_too_long_maps_to_400(conv_app, monkeypatch):
    import api.routers.conversations as conv_router

    monkeypatch.setenv("ASK_FOLLOWUP_ENABLED", "true")
    app, agency, uid, pool = conv_app

    async def _fake_answer_followup(*, question, context_tool, context_args, context_result, locale):
        return None, "too_long"

    monkeypatch.setattr(conv_router._followup, "answer_followup", _fake_answer_followup)

    async with _authed_client(app, uid) as c:
        cr = await c.post(f"/api/{agency}/conversations", json={"title": "T", "filter_ctx": {}}, headers=_CSRF)
        conv_id = cr.json()["conversation_id"]
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
            rendered_summary="prior answer",
        )
    async with _authed_optional_client(app, uid) as c:
        r = await c.post(
            f"/api/{agency}/conversations/{conv_id}/followup",
            json={"question": "q", "context_message_id": msg["message_id"]},
            headers=_CSRF,
        )
    assert r.status_code == 400
    assert r.json()["detail"] == "question_too_long"


@pytest.mark.asyncio
async def test_followup_authed_real_long_question_maps_to_400_not_422(conv_app, monkeypatch):
    """End-to-end version of the test above: a genuinely oversized question,
    not a mocked `answer_followup` return value. `FollowupBody.question`
    must NOT declare a Pydantic `max_length` -- that would 422 before this
    handler's own too_long check (and its friendly frontend copy) ever run."""
    import api.routers.conversations as conv_router

    monkeypatch.setenv("ASK_FOLLOWUP_ENABLED", "true")
    app, agency, uid, pool = conv_app

    async with _authed_client(app, uid) as c:
        cr = await c.post(f"/api/{agency}/conversations", json={"title": "T", "filter_ctx": {}}, headers=_CSRF)
        conv_id = cr.json()["conversation_id"]
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
            rendered_summary="prior answer",
        )
    long_question = "a" * (conv_router._followup.MAX_QUESTION_CHARS + 1)
    async with _authed_optional_client(app, uid) as c:
        r = await c.post(
            f"/api/{agency}/conversations/{conv_id}/followup",
            json={"question": long_question, "context_message_id": msg["message_id"]},
            headers=_CSRF,
        )
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == "question_too_long"


@pytest.mark.asyncio
async def test_followup_authed_llm_error_maps_to_502(conv_app, monkeypatch):
    import api.routers.conversations as conv_router

    monkeypatch.setenv("ASK_FOLLOWUP_ENABLED", "true")
    app, agency, uid, pool = conv_app

    async def _fake_answer_followup(*, question, context_tool, context_args, context_result, locale):
        return None, "provider_unavailable"

    monkeypatch.setattr(conv_router._followup, "answer_followup", _fake_answer_followup)

    async with _authed_client(app, uid) as c:
        cr = await c.post(f"/api/{agency}/conversations", json={"title": "T", "filter_ctx": {}}, headers=_CSRF)
        conv_id = cr.json()["conversation_id"]
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
            rendered_summary="prior answer",
        )
    async with _authed_optional_client(app, uid) as c:
        r = await c.post(
            f"/api/{agency}/conversations/{conv_id}/followup",
            json={"question": "q", "context_message_id": msg["message_id"]},
            headers=_CSRF,
        )
    assert r.status_code == 502
    assert r.json()["detail"] == "llm_error:provider_unavailable"


@pytest.mark.asyncio
async def test_followup_authed_success_appends_messages(conv_app, monkeypatch):
    import api.routers.conversations as conv_router

    monkeypatch.setenv("ASK_FOLLOWUP_ENABLED", "true")
    app, agency, uid, pool = conv_app

    captured = {}

    async def _fake_answer_followup(*, question, context_tool, context_args, context_result, locale):
        captured["context_tool"] = context_tool
        captured["context_args"] = context_args
        captured["context_result"] = context_result
        return "the answer", None

    monkeypatch.setattr(conv_router._followup, "answer_followup", _fake_answer_followup)

    async with _authed_client(app, uid) as c:
        cr = await c.post(f"/api/{agency}/conversations", json={"title": "T", "filter_ctx": {}}, headers=_CSRF)
        conv_id = cr.json()["conversation_id"]
    async with pool.acquire() as conn:
        msg = await conv_router._conv.append_message(
            conn,
            conv_id,
            role="assistant",
            chip_id=None,
            tool="describe_data",
            args={"kind": "stops"},
            signature_hash=None,
            result={"ok": True},
            rendered_summary="prior answer",
        )
    async with _authed_optional_client(app, uid) as c:
        r = await c.post(
            f"/api/{agency}/conversations/{conv_id}/followup",
            json={"question": "what about X?", "context_message_id": msg["message_id"]},
            headers=_CSRF,
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user"]["rendered_summary"] == "what about X?"
    assert body["assistant"]["rendered_summary"] == "the answer"
    assert body["assistant"]["args"] == {"context_message_id": msg["message_id"]}
    assert captured["context_tool"] == "describe_data"
    assert captured["context_args"] == {"kind": "stops"}
    assert captured["context_result"] == {"ok": True}

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT role, rendered_summary FROM ask_conversation_messages "
            "WHERE conversation_id = $1 ORDER BY created_at",
            conv_id,
        )
    assert [(r["role"], r["rendered_summary"]) for r in rows] == [
        ("assistant", "prior answer"),
        ("user", "what about X?"),
        ("assistant", "the answer"),
    ]


@pytest.mark.asyncio
async def test_followup_anon_requires_inline_context_400(conv_app, monkeypatch):
    monkeypatch.setenv("ASK_FOLLOWUP_ENABLED", "true")
    app, agency, uid, _pool = conv_app
    async with _authed_client(app, uid) as c:
        cr = await c.post(f"/api/{agency}/conversations", json={"title": "T", "filter_ctx": {}}, headers=_CSRF)
        conv_id = cr.json()["conversation_id"]
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post(
            f"/api/{agency}/conversations/{conv_id}/followup",
            json={"question": "q"},
            headers=_CSRF,
        )
    assert r.status_code == 400
    assert "inline context" in r.json()["detail"]


@pytest.mark.asyncio
async def test_followup_anon_success_returns_synthetic_messages(conv_app, monkeypatch):
    import api.routers.conversations as conv_router

    monkeypatch.setenv("ASK_FOLLOWUP_ENABLED", "true")
    app, agency, uid, _pool = conv_app

    async def _fake_answer_followup(*, question, context_tool, context_args, context_result, locale):
        return "anon answer", None

    monkeypatch.setattr(conv_router._followup, "answer_followup", _fake_answer_followup)

    async with _authed_client(app, uid) as c:
        cr = await c.post(f"/api/{agency}/conversations", json={"title": "T", "filter_ctx": {}}, headers=_CSRF)
        conv_id = cr.json()["conversation_id"]
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post(
            f"/api/{agency}/conversations/{conv_id}/followup",
            json={
                "question": "anon q",
                "context_tool": "describe_data",
                "context_args": {"kind": "stops"},
                "context_result": {"ok": True},
            },
            headers=_CSRF,
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user"]["rendered_summary"] == "anon q"
    assert body["assistant"]["rendered_summary"] == "anon answer"
    assert body["user"]["message_id"] < 0  # synthetic, not persisted
    # Anon path never persists — nothing was written to the DB for this conv.
    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT 1 FROM ask_conversation_messages WHERE conversation_id = $1",
            conv_id,
        )
    assert rows == []


@pytest.mark.asyncio
async def test_followup_enabled_endpoint_reports_flag_and_max_chars(conv_app, monkeypatch):
    """The frontend's input maxLength is sourced from this endpoint (not
    hand-mirrored) so it can never drift from MAX_QUESTION_CHARS."""
    import api.routers.conversations as conv_router

    monkeypatch.setenv("ASK_FOLLOWUP_ENABLED", "true")
    app, agency, _uid, _pool = conv_app
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get(f"/api/{agency}/ask/followup-enabled")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["enabled"] is True
    assert body["max_question_chars"] == conv_router._followup.MAX_QUESTION_CHARS
