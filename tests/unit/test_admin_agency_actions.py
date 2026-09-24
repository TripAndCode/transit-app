"""Operator actions on the admin agency page refuse work they cannot do.

Exercised through a minimal standalone app (not ``api.main``) with
``require_admin``/``get_conn`` overridden, so nothing here touches a real
pool, database, or feed -- the same pattern as ``tests/unit/test_admin_bounds.py``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.deps import get_conn
from api.routers import admin_agencies
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


_RUN_ROW = {
    "run_id": 77,
    "kind": "ingest",
    "agency_id": 1,
    "agency_name": "Hokuriku",
    "started_at": datetime(2026, 9, 21, 4, 30, tzinfo=timezone.utc),
    "finished_at": None,
    "status": "running",
    "rows": None,
    "lock_wait_ms": None,
    "error": None,
    "requested_by": 1,
}


class _Conn:
    def __init__(self, deleted_at: datetime | None, *, insert_unrecordable: bool = False):
        self._row = {
            "agency_id": 1,
            "agency_name": "Hokuriku",
            "feed_url": "https://example.test/feed.pb",
            "ingest_strategy": "direct_url",
            "deleted_at": deleted_at,
        }
        self.insert_unrecordable = insert_unrecordable
        self.inserted: list[tuple[Any, ...]] = []
        self.audit: list[tuple[Any, ...]] = []

    async def fetchrow(self, sql: str = "", *args: Any, **_kwargs: Any) -> dict[str, Any] | None:
        if "INSERT INTO pipeline_runs" in sql:
            self.inserted.append(args)
            return None if self.insert_unrecordable else dict(_RUN_ROW)
        return self._row

    async def execute(self, sql: str, *args: Any) -> str:
        """The audit seam writes a real row now; capture it rather than
        running it."""
        assert "INSERT INTO admin_audit" in sql, f"unexpected execute: {sql}"
        self.audit.append(args)
        return "INSERT 1"


def _client(conn: _Conn) -> TestClient:
    app = FastAPI()
    app.include_router(admin_agencies.router)
    app.dependency_overrides[require_admin] = lambda: _ADMIN
    app.dependency_overrides[get_conn] = lambda: conn
    return TestClient(app)


@pytest.fixture(autouse=True)
def _no_csrf(monkeypatch):
    monkeypatch.setattr(admin_agencies, "csrf_guard", lambda _request: None)


@pytest.fixture(autouse=True)
def queued(monkeypatch) -> list[int]:
    """Stand in for the real runner. TestClient executes background tasks, and
    the real one opens `DATABASE_URL` and ingests -- which, with a developer's
    shell pointed at the dev database, is a write against real data from a
    unit test."""
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "api.routers.internal._run_ingest_and_analyze",
        lambda **kwargs: calls.append(kwargs),
        raising=False,
    )
    return calls


def test_reanalyzing_a_disabled_agency_is_refused_rather_than_reported_started(queued):
    """The runner selects on ``deleted_at IS NULL``, so a queued run would
    find no agency and do nothing while the operator was told it started."""
    conn = _Conn(deleted_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    response = _client(conn).post("/api/admin/agencies/1/reanalyze")

    assert response.status_code == 409
    assert queued == []


def test_reanalyzing_a_live_agency_is_accepted(queued):
    conn = _Conn(deleted_at=None)
    response = _client(conn).post("/api/admin/agencies/1/reanalyze")
    assert response.status_code == 202
    assert response.json() == {"status": "started"}
    assert [call["agency_ids"] for call in queued] == [[1]]
    assert [call[1] for call in conn.audit] == ["agency.reanalyze_requested"]


def test_reanalyzing_opens_one_umbrella_run_and_hands_the_runner_its_id(queued):
    """Without a row opened here the drawer's action draws no bar at all, and
    the per-agency rows the sweep writes have no run to belong to."""
    conn = _Conn(deleted_at=None)
    _client(conn).post("/api/admin/agencies/1/reanalyze")

    assert conn.inserted == [("ingest", 1, _ADMIN.user_id)]
    assert queued[0]["run_id"] == 77


def test_a_reanalyze_that_cannot_be_recorded_is_refused_rather_than_run_blind(queued):
    conn = _Conn(deleted_at=None, insert_unrecordable=True)
    response = _client(conn).post("/api/admin/agencies/1/reanalyze")

    assert response.status_code == 503
    assert queued == []
