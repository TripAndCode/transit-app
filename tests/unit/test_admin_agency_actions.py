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


class _Conn:
    def __init__(self, deleted_at: datetime | None):
        self._row = {
            "agency_id": 1,
            "agency_name": "Hokuriku",
            "feed_url": "https://example.test/feed.pb",
            "ingest_strategy": "direct_url",
            "deleted_at": deleted_at,
        }

    async def fetchrow(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return self._row


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
    calls: list[int] = []
    monkeypatch.setattr(
        "api.routers.internal._run_ingest_and_analyze", lambda agency_id: calls.append(agency_id), raising=False
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
    response = _client(_Conn(deleted_at=None)).post("/api/admin/agencies/1/reanalyze")
    assert response.status_code == 202
    assert response.json() == {"status": "started"}
    assert queued == [1]
