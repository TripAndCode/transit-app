"""`GET /api/admin/runs` and `POST /api/admin/runs` wiring.

Exercised through a minimal standalone app with ``require_admin``/``get_conn``
overridden, so nothing here touches a real pool, database or background job —
the same pattern as ``tests/unit/test_admin_board_endpoint.py``.

The properties pinned here are the ones a reader of the endpoint cannot
verify by eye: the manual trigger opens its row *before* answering (so the
202's run_id is real and the operator's bar appears at once), it writes an
audit action, an unavailable ``pipeline_runs`` table degrades the board to an
empty timeline instead of a 500, and the background sweep is handed the
requester so its rows are attributable.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.deps import get_conn
from api.routers import admin as admin_router
from api.security import User, require_admin
from tests.conftest import TEST_ORIGIN

_ADMIN = User(
    user_id=9,
    email="admin@example.com",
    name="Admin",
    avatar_url=None,
    role="admin",
    suspended_at=None,
    llm_approved=True,
)

_RUN_ROW = {
    "run_id": 3,
    "kind": "analyze",
    "agency_id": 1,
    "agency_name": "Hokuriku",
    "started_at": datetime(2026, 9, 21, 4, 30, tzinfo=timezone.utc),
    "finished_at": None,
    "status": "running",
    "rows": None,
    "lock_wait_ms": None,
    "error": None,
    "requested_by": None,
}


class _Conn:
    """Fake asyncpg connection answering each query the runs routes make."""

    def __init__(self, *, runs_error=None, agency_exists=True, insert_unrecordable=False):
        self.runs_error = runs_error
        self.agency_exists = agency_exists
        self.insert_unrecordable = insert_unrecordable
        self.inserted: list[tuple] = []

    def transaction(self):
        """The trigger route wraps its row-open and its audit entry
        together; this fake has no rollback to model, so the block just
        runs -- same pattern as ``tests/unit/test_admin_user_drawer_endpoints.py``."""

        @asynccontextmanager
        async def _noop():
            yield

        return _noop()

    async def fetch(self, sql, *args):
        if "pipeline_runs" in sql:
            if self.runs_error is not None:
                raise self.runs_error
            return [_RUN_ROW]
        if "schema_migrations" in sql:
            return [{"version": "0001"}]
        return []

    async def fetchrow(self, sql, *args):
        if "INSERT INTO pipeline_runs" in sql:
            self.inserted.append(args)
            if self.insert_unrecordable:
                return None
            return {**_RUN_ROW, "run_id": 77, "kind": args[0], "agency_id": args[1], "requested_by": args[2]}
        return None

    async def fetchval(self, sql, *args):
        if "FROM agencies" in sql:
            return 1 if self.agency_exists else None
        return 0


@pytest.fixture
def scheduled(monkeypatch):
    """Capture what the route hands to BackgroundTasks instead of running it."""
    calls: list[dict] = []

    def _capture(*, background_tasks, **kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(admin_router, "_start_manual_run", _capture)
    return calls


@pytest.fixture
def audited(monkeypatch):
    recorded: list[dict] = []

    async def _record(conn, **kwargs):
        recorded.append(kwargs)

    monkeypatch.setattr(admin_router, "record_admin_action", _record)
    return recorded


def _client(conn: _Conn) -> TestClient:
    app = FastAPI()
    app.include_router(admin_router.router)
    app.dependency_overrides[require_admin] = lambda: _ADMIN
    app.dependency_overrides[get_conn] = lambda: conn
    return TestClient(app)


@pytest.fixture(autouse=True)
def _no_real_collectors(monkeypatch):
    async def _none():
        return []

    monkeypatch.setattr(admin_router, "_collect_documents", _none)


@pytest.fixture(autouse=True)
def _no_reaper(monkeypatch):
    """The board sweeps abandoned runs on its own connection; a unit test
    must not open one."""
    monkeypatch.setattr(admin_router, "_last_reap_at", None, raising=False)
    monkeypatch.setattr(admin_router, "reap_abandoned_runs_best_effort", lambda _db_url: 0, raising=False)


def test_the_day_listing_returns_the_shaped_runs_for_the_requested_day():
    body = _client(_Conn()).get("/api/admin/runs?date=2026-09-21").json()
    assert body["date"] == "2026-09-21"
    assert body["runs"][0]["run_id"] == 3
    assert body["runs"][0]["agency_name"] == "Hokuriku"
    assert body["runs"][0]["started_at"].endswith("Z")


def test_an_unparseable_date_is_rejected_rather_than_silently_meaning_today():
    assert _client(_Conn()).get("/api/admin/runs?date=yesterday").status_code == 400


def test_an_unavailable_runs_table_gives_an_empty_timeline_not_a_500():
    response = _client(_Conn(runs_error=RuntimeError("no pipeline_runs"))).get("/api/admin/runs")
    assert response.status_code == 200
    assert response.json()["runs"] == []


def test_the_board_carries_todays_runs_so_one_poll_covers_the_whole_page():
    body = _client(_Conn()).get("/api/admin/board").json()
    assert [run["run_id"] for run in body["runs"]] == [3]


def test_a_board_whose_runs_table_is_missing_still_renders_every_other_section():
    body = _client(_Conn(runs_error=RuntimeError("no pipeline_runs"))).get("/api/admin/board").json()
    assert body["runs"] == []
    assert len(body["collectors"]) == 4


def test_triggering_a_run_opens_its_row_and_answers_202_with_that_id(scheduled, audited):
    conn = _Conn()
    response = _client(conn).post(
        "/api/admin/runs", headers={"Origin": TEST_ORIGIN}, json={"kind": "analyze", "agency_id": 1}
    )
    assert response.status_code == 202
    assert response.json()["runs"][0]["run_id"] == 77
    assert conn.inserted == [("analyze", 1, _ADMIN.user_id)]


def test_the_triggered_sweep_is_handed_the_requester_and_the_row_it_must_close(scheduled, audited):
    _client(_Conn()).post("/api/admin/runs", headers={"Origin": TEST_ORIGIN}, json={"kind": "ingest"})
    assert scheduled == [{"kind": "ingest", "agency_ids": None, "requested_by": 9, "run_id": 77, "run_weather": False}]


def test_a_manual_run_does_not_re_drive_the_fleet_weather_fetch(scheduled, audited):
    """Observed weather is a third-party fetch on a fleet-wide schedule; an
    operator asking for a re-aggregation has no reason to trigger one."""
    _client(_Conn()).post("/api/admin/runs", headers={"Origin": TEST_ORIGIN}, json={"kind": "ingest"})
    assert scheduled[0]["run_weather"] is False


def test_an_environment_that_cannot_record_the_row_is_refused_not_silently_run(scheduled, audited):
    """Without a run row the operator gets a bar-less 202 and no way to tell
    whether anything happened, so the trigger declines instead."""
    response = _client(_Conn(insert_unrecordable=True)).post(
        "/api/admin/runs", headers={"Origin": TEST_ORIGIN}, json={"kind": "ingest"}
    )
    assert response.status_code == 503
    assert scheduled == []


def test_the_listing_publishes_the_lock_column_as_a_probe_cost(scheduled, audited):
    run = _client(_Conn()).get("/api/admin/runs").json()["runs"][0]
    assert "lock_probe_ms" in run
    assert "lock_wait_ms" not in run


def test_a_single_agency_request_restricts_the_sweep_to_that_agency(scheduled, audited):
    _client(_Conn()).post("/api/admin/runs", headers={"Origin": TEST_ORIGIN}, json={"kind": "ingest", "agency_id": 4})
    assert scheduled[0]["agency_ids"] == [4]


def test_triggering_a_run_is_audited(scheduled, audited):
    _client(_Conn()).post("/api/admin/runs", headers={"Origin": TEST_ORIGIN}, json={"kind": "analyze", "agency_id": 1})
    assert audited[0]["actor_id"] == 9
    assert audited[0]["action"] == "pipeline.run"
    assert audited[0]["target_id"] == "1"


def test_a_kind_this_endpoint_cannot_actually_perform_is_rejected(scheduled, audited):
    response = _client(_Conn()).post("/api/admin/runs", headers={"Origin": TEST_ORIGIN}, json={"kind": "weather"})
    assert response.status_code == 400
    assert scheduled == []


def test_an_unknown_agency_is_rejected_before_anything_is_scheduled(scheduled, audited):
    response = _client(_Conn(agency_exists=False)).post(
        "/api/admin/runs", headers={"Origin": TEST_ORIGIN}, json={"kind": "ingest", "agency_id": 99}
    )
    assert response.status_code == 404
    assert scheduled == []
