"""Router-shape tests for admin Ask-ops: the funnel's default date window.

Exercised through a minimal standalone app with ``require_admin``/``get_conn``
overridden to a fake in-memory connection -- the same pattern as
``tests/unit/test_admin_agency_actions.py`` -- so nothing here touches a real
pool or database.
"""

from __future__ import annotations

from datetime import date

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.deps import get_conn
from api.routers import admin_ask
from api.security import require_admin
from tests.fixtures.users import admin_user

_ADMIN = admin_user()


class _CaptureConn:
    """Records every ``fetch`` call; returns no rows, since only the SQL and
    bound args matter for these tests."""

    def __init__(self):
        self.calls: list[tuple[str, tuple]] = []

    async def fetch(self, sql, *args):
        self.calls.append((sql, args))
        return []


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
