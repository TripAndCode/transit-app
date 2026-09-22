"""`GET /api/admin/board` wiring: every sub-check degrades on its own.

Exercised through a minimal standalone app (not ``api.main``) with
``require_admin``/``get_conn`` overridden, so nothing here touches a real
pool, database, or collector subprocess — the same pattern as
``tests/unit/test_admin_bounds.py``.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.admin_board import BOARD_WINDOW_DAYS, COLLECTOR_ORDER
from api.deps import get_conn
from api.routers import admin as admin_router
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

_NOW = datetime.now(timezone.utc)
_YESTERDAY = (_NOW - timedelta(days=1)).date()

_JOIN_ROWS = [
    {
        "agency_id": 1,
        "agency_name": "Hokuriku",
        "analyzed_at": _NOW,
        "date": _YESTERDAY,
        "raw_samples": 10_000,
        "clamp_count": 300,
    }
]
_AGENCY_ONLY_ROWS = [{"agency_id": 1, "agency_name": "Hokuriku"}]


class _Conn:
    """Fake asyncpg connection answering each of the board's queries by shape."""

    def __init__(self, *, approvals=0, join_error=None, agency_error=None, fetchval_error=None):
        self.approvals = approvals
        self.join_error = join_error
        self.agency_error = agency_error
        self.fetchval_error = fetchval_error

    async def fetch(self, sql, *args):
        if "schema_migrations" in sql:
            return [{"version": "0001"}]
        if "agg_feed_health" in sql:
            if self.join_error is not None:
                raise self.join_error
            return _JOIN_ROWS
        if self.agency_error is not None:
            raise self.agency_error
        return _AGENCY_ONLY_ROWS

    async def fetchval(self, sql, *args):
        if self.fetchval_error is not None:
            raise self.fetchval_error
        return self.approvals


def _client(conn: _Conn) -> TestClient:
    app = FastAPI()
    app.include_router(admin_router.router)
    app.dependency_overrides[require_admin] = lambda: _ADMIN
    app.dependency_overrides[get_conn] = lambda: conn
    return TestClient(app)


def _stub_documents(documents):
    async def _collect():
        return documents

    return _collect


#: Captured before `_no_real_collectors` can replace it, so the few tests
#: that are *about* the collection path still exercise the real function
#: instead of silently asserting against the stub.
_REAL_COLLECT_DOCUMENTS = admin_router._collect_documents


@pytest.fixture(autouse=True)
def _no_real_collectors(monkeypatch):
    """A unit test must never shell out to the real collectors."""
    monkeypatch.setattr(admin_router, "_collect_documents", _stub_documents([]))


def test_board_returns_every_section():
    body = _client(_Conn()).get("/api/admin/board").json()
    assert [t["key"] for t in body["collectors"]] == list(COLLECTOR_ORDER)
    assert len(body["freshness"]) == 1
    assert len(body["freshness"][0]["days"]) == BOARD_WINDOW_DAYS
    assert body["migrations"]["applied"] == "0001"
    assert isinstance(body["alerts"], list)


def test_collector_failure_degrades_to_unknown_tiles_not_an_error(monkeypatch):
    def _boom():
        raise RuntimeError("gh exploded")

    # Patched below `_collect_documents` so the endpoint runs its real guard.
    monkeypatch.undo()
    monkeypatch.setattr(admin_router, "_collect_all", _boom)
    response = _client(_Conn()).get("/api/admin/board")
    assert response.status_code == 200
    assert {t["status"] for t in response.json()["collectors"]} == {"unknown"}


def test_a_healthy_collector_reaches_the_tile(monkeypatch):
    documents = [
        {
            "component": "r2",
            "state": "healthy",
            "last_success_at": _NOW.isoformat().replace("+00:00", "Z"),
            "details": {},
        }
    ]
    monkeypatch.setattr(admin_router, "_collect_documents", _stub_documents(documents))
    tiles = {t["key"]: t for t in _client(_Conn()).get("/api/admin/board").json()["collectors"]}
    assert tiles["r2"]["status"] == "ok"
    assert sum(tiles["r2"]["history"]) == 24


def test_a_missing_feed_health_table_falls_back_to_the_plain_agency_list():
    conn = _Conn(join_error=asyncpg.UndefinedTableError("no agg_feed_health"))
    body = _client(conn).get("/api/admin/board").json()
    assert [row["agency_name"] for row in body["freshness"]] == ["Hokuriku"]
    assert {d["state"] for d in body["freshness"][0]["days"]} == {"missing"}


def test_a_total_query_failure_still_answers_with_an_empty_heatmap():
    conn = _Conn(join_error=RuntimeError("down"), agency_error=RuntimeError("down"))
    response = _client(conn).get("/api/admin/board")
    assert response.status_code == 200
    assert response.json()["freshness"] == []


def test_a_failing_approvals_count_raises_no_approvals_alert():
    conn = _Conn(fetchval_error=RuntimeError("no users table"))
    codes = [a["code"] for a in _client(conn).get("/api/admin/board").json()["alerts"]]
    assert "llm_approvals_pending" not in codes


def test_pending_approvals_surface_as_an_info_alert():
    alerts = {a["code"]: a for a in _client(_Conn(approvals=2)).get("/api/admin/board").json()["alerts"]}
    assert alerts["llm_approvals_pending"]["level"] == "info"
    assert alerts["llm_approvals_pending"]["params"]["count"] == 2


def test_clamped_samples_above_the_threshold_surface_as_a_warn_alert():
    alerts = {a["code"]: a for a in _client(_Conn()).get("/api/admin/board").json()["alerts"]}
    assert alerts["clamp_high"]["level"] == "warn"


async def test_collectors_are_abandoned_once_the_budget_expires(monkeypatch):
    """A wedged collector costs the endpoint its budget, not the request."""
    import time

    def _hang():
        # Long relative to the 0.05s budget below, short enough that the
        # abandoned thread does not hold up interpreter shutdown.
        time.sleep(2)
        return ["never"]

    monkeypatch.setattr(admin_router, "_COLLECTOR_BUDGET_SECONDS", 0.05)
    monkeypatch.setattr(admin_router, "_collect_all", _hang)
    admin_router._collector_task = None
    try:
        loop = asyncio.get_running_loop()
        started = loop.time()
        assert await _REAL_COLLECT_DOCUMENTS() == []
        assert loop.time() - started < 5
    finally:
        admin_router._collector_task = None


async def test_only_one_collection_runs_however_many_polls_arrive(monkeypatch):
    """The default executor is shared with the embedder and the LLM calls
    and holds only a handful of threads, so a board left open in several
    tabs must not spend them all on abandoned collections."""
    import threading
    import time

    started = 0
    lock = threading.Lock()

    def _slow():
        nonlocal started
        with lock:
            started += 1
        time.sleep(0.4)
        return []

    monkeypatch.setattr(admin_router, "_COLLECTOR_BUDGET_SECONDS", 0.05)
    monkeypatch.setattr(admin_router, "_collect_all", _slow)
    admin_router._collector_task = None
    try:
        results = await asyncio.gather(*(_REAL_COLLECT_DOCUMENTS() for _ in range(6)))
        assert results == [[]] * 6  # every poll gave up on its own budget

        shared = admin_router._collector_task
        assert shared is not None
        await shared  # the abandoned collection is still the only one running
        assert started == 1
    finally:
        admin_router._collector_task = None


def test_the_collectors_read_the_repo_the_environment_points_at(monkeypatch, tmp_path):
    """The collectors' own default is the VPS's checkout path; anywhere else
    -- the API container included -- the tree is elsewhere or absent."""
    seen: dict[str, object] = {}

    class _Stub:
        @staticmethod
        def collect_all(**kwargs):
            seen.update(kwargs)
            return []

    import scripts

    monkeypatch.setattr(scripts, "ops_status_page", _Stub, raising=False)
    monkeypatch.setenv(admin_router._OPS_STATUS_REPO_ENV, str(tmp_path))
    admin_router._collect_all()
    assert seen["local_repo"] == tmp_path

    seen.clear()
    monkeypatch.delenv(admin_router._OPS_STATUS_REPO_ENV)
    admin_router._collect_all()
    assert "local_repo" not in seen
