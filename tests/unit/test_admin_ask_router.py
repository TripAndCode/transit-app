"""Router-shape tests for admin Ask-ops: the funnel's default date window
and the embedder load being moved off the event loop.

Exercised through a minimal standalone app with ``require_admin``/``get_conn``
overridden to a fake in-memory connection -- the same pattern as
``tests/unit/test_admin_agency_actions.py`` -- so nothing here touches a real
pool, database, or ML embedder.
"""

from __future__ import annotations

import asyncio
from datetime import date

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.deps import get_conn
from api.routers import admin_ask
from api.security import User, require_admin

_ADMIN = User(
    user_id=1,
    email="admin@example.com",
    name="Admin",
    avatar_url=None,
    role="admin",
    suspended_at=None,
    llm_approved=True,
)

_ORIGIN = {"Origin": "http://test"}


class _CaptureConn:
    """Records every ``fetch`` call; returns no rows, since only the SQL and
    bound args matter for these tests."""

    def __init__(self):
        self.calls: list[tuple[str, tuple]] = []

    async def fetch(self, sql, *args):
        self.calls.append((sql, args))
        return []


class _PromoteConn:
    """Just enough to reach the embedder-availability check in
    ``promote_query_log`` -- an unavailable embedder 503s before anything
    else on the connection is touched."""

    def __init__(self, log_row):
        self._log_row = log_row

    async def fetchrow(self, sql, *_args):
        if "FROM ask_query_log WHERE id=$1" in sql:
            return self._log_row
        raise AssertionError(f"unexpected fetchrow: {sql}")


def _client(conn) -> TestClient:
    app = FastAPI()
    app.include_router(admin_ask.router)
    app.dependency_overrides[require_admin] = lambda: _ADMIN
    app.dependency_overrides[get_conn] = lambda: conn
    return TestClient(app)


# ── funnel default window (A11) ─────────────────────────────────────────────


def test_funnel_defaults_to_the_last_30_jst_days_when_both_bounds_omitted(monkeypatch):
    monkeypatch.setattr(admin_ask, "jst_today", lambda: date(2026, 9, 24))
    conn = _CaptureConn()

    r = _client(conn).get("/api/admin/ask/funnel")

    assert r.status_code == 200
    assert len(conn.calls) == 1
    _sql, args = conn.calls[0]
    assert args == (date(2026, 8, 26), date(2026, 9, 24))


def test_funnel_does_not_default_the_other_bound_when_one_is_given(monkeypatch):
    monkeypatch.setattr(admin_ask, "jst_today", lambda: date(2026, 9, 24))
    conn = _CaptureConn()

    r = _client(conn).get("/api/admin/ask/funnel", params={"from": "2026-01-01"})

    assert r.status_code == 200
    _sql, args = conn.calls[0]
    assert args == (date(2026, 1, 1),), "providing one bound must not fill in the other with the 30-day default"


def test_funnel_runs_unbounded_when_both_bounds_are_given(monkeypatch):
    monkeypatch.setattr(admin_ask, "jst_today", lambda: date(2026, 9, 24))
    conn = _CaptureConn()

    r = _client(conn).get("/api/admin/ask/funnel", params={"from": "2020-01-01", "to": "2020-01-31"})

    assert r.status_code == 200
    _sql, args = conn.calls[0]
    assert args == (date(2020, 1, 1), date(2020, 1, 31))


# ── embedder load off the event loop (A20) ──────────────────────────────────


def test_promote_loads_the_embedder_via_asyncio_to_thread(monkeypatch):
    calls: list[object] = []
    real_to_thread = asyncio.to_thread

    async def _spy_to_thread(fn, *args, **kwargs):
        calls.append(fn)
        return await real_to_thread(fn, *args, **kwargs)

    monkeypatch.setattr(admin_ask.asyncio, "to_thread", _spy_to_thread)
    monkeypatch.setattr(admin_ask, "csrf_guard", lambda _request: None)

    class _UnavailableEmbedder:
        available = False

    monkeypatch.setattr(admin_ask, "get_embedder", lambda: _UnavailableEmbedder())
    conn = _PromoteConn({"agency_id": 1, "signature_hash": "abc"})

    r = _client(conn).post("/api/admin/ask/promote", json={"query_log_id": 1}, headers=_ORIGIN)

    assert r.status_code == 503
    assert calls == [admin_ask.get_embedder], "get_embedder must be called through asyncio.to_thread"
