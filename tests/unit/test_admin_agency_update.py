"""`POST .../refresh-static` and `POST .../update` refuse work they cannot do,
and hand the background runner a run it can be watched by.

Exercised through a minimal standalone app (not ``api.main``) with
``require_admin``/``get_conn`` overridden, the same pattern as
``tests/unit/test_admin_agency_actions.py``; the end-to-end path through a
real database, session and CSRF check is ``tests/api/test_admin_agency_update.py``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.routers.admin_agencies as admin_agencies
from api.deps import get_conn
from api.security import require_admin
from pipeline.locks import AGENCY_RUN_REQUEST_LOCK_KEY
from tests.fixtures.pool import CountingPool
from tests.fixtures.users import admin_user

_ADMIN = admin_user()


class _Txn:
    def __init__(self, conn: "_Conn"):
        self._conn = conn

    async def __aenter__(self) -> "_Txn":
        self._conn.events.append("begin")
        return self

    async def __aexit__(self, *exc: object) -> bool:
        self._conn.events.append("end")
        return False


class _Conn:
    def __init__(
        self,
        *,
        deleted_at: datetime | None = None,
        static_url: str | None = "https://example.test/gtfs.zip",
        static_strategy: str | None = "direct_url",
        in_flight: bool = False,
        insert_unrecordable: bool = False,
    ):
        self._row = {
            "agency_id": 1,
            "agency_name": "Hiroshima Bus",
            "feed_url": "https://example.test/feed.pb",
            "static_url": static_url,
            "static_strategy": static_strategy,
            "ingest_strategy": "static_join",
            "deleted_at": deleted_at,
        }
        self.in_flight = in_flight
        self.insert_unrecordable = insert_unrecordable
        self.inserted: list[tuple[Any, ...]] = []
        self.audit: list[tuple[Any, ...]] = []
        self.events: list[str] = []

    def transaction(self) -> _Txn:
        return _Txn(self)

    async def fetchrow(self, sql: str = "", *args: Any) -> dict[str, Any] | None:
        if "INSERT INTO pipeline_runs" in sql:
            self.inserted.append(args)
            self.events.append("insert")
            if self.insert_unrecordable:
                return None
            return {
                "run_id": 77,
                "kind": args[0],
                "agency_id": args[1],
                "agency_name": "Hiroshima Bus",
                "started_at": datetime(2026, 10, 13, 5, 0, tzinfo=timezone.utc),
                "finished_at": None,
                "status": "running",
                "rows": None,
                "lock_wait_ms": None,
                "error": None,
                "requested_by": args[2],
            }
        return self._row

    async def fetchval(self, sql: str, *args: Any) -> Any:
        assert "status = 'running'" in sql, f"unexpected fetchval: {sql}"
        self.events.append("in_flight_check")
        return 1 if self.in_flight else None

    async def execute(self, sql: str, *args: Any) -> str:
        if "pg_advisory_xact_lock" in sql:
            self.events.append(f"lock:{args}")
            return "SELECT 1"
        assert "INSERT INTO admin_audit" in sql, f"unexpected execute: {sql}"
        self.audit.append(args)
        return "INSERT 1"


def _client(conn: _Conn, pool: CountingPool | None = None) -> TestClient:
    app = FastAPI()
    app.include_router(admin_agencies.router)
    app.dependency_overrides[require_admin] = lambda: _ADMIN
    app.dependency_overrides[get_conn] = lambda: conn
    app.state.pool = pool or CountingPool(conn)
    return TestClient(app)


@pytest.fixture(autouse=True)
def _no_csrf(monkeypatch):
    monkeypatch.setattr(admin_agencies, "csrf_guard", lambda _request: None)


@pytest.fixture(autouse=True)
def queued(monkeypatch) -> list[dict[str, Any]]:
    """Stand in for the real runner: TestClient executes background tasks,
    and the real one downloads a timetable and writes `DATABASE_URL`."""
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "api.routers.internal._run_agency_update",
        lambda **kwargs: calls.append(kwargs),
        raising=False,
    )
    monkeypatch.setattr(
        "api.routers.internal._run_ingest_and_analyze",
        lambda **kwargs: calls.append({"reanalyze": kwargs}),
        raising=False,
    )
    return calls


_ROUTES = [
    ("/api/admin/agencies/1/refresh-static", "static", "agency.static_refresh_requested", False),
    ("/api/admin/agencies/1/update", "update", "agency.update_requested", True),
]


@pytest.mark.parametrize(("path", "kind", "action", "chained"), _ROUTES)
def test_a_run_is_opened_audited_and_handed_to_the_runner(queued, path, kind, action, chained):
    conn = _Conn()
    response = _client(conn).post(path)

    assert response.status_code == 202, response.text
    assert response.json() == {"status": "started", "run_id": 77}
    assert conn.inserted == [(kind, 1, _ADMIN.user_id)]
    assert [call[1] for call in conn.audit] == [action]
    assert queued == [
        {"agency_id": 1, "requested_by": _ADMIN.user_id, "run_id": 77, "then_ingest_and_analyze": chained}
    ]


@pytest.mark.parametrize(("path", "kind", "action", "chained"), _ROUTES)
def test_a_second_run_while_one_is_in_flight_is_refused(queued, path, kind, action, chained):
    conn = _Conn(in_flight=True)
    response = _client(conn).post(path)

    assert response.status_code == 409
    assert conn.inserted == []
    assert conn.audit == []
    assert queued == []


@pytest.mark.parametrize(("path", "kind", "action", "chained"), _ROUTES)
def test_the_in_flight_check_and_the_insert_share_one_per_agency_lock(queued, path, kind, action, chained):
    """Two presses arriving together must not both see "nothing running"
    and both start: the check and the insert run in one transaction under a
    per-agency advisory lock that the second request waits on."""
    conn = _Conn()
    _client(conn).post(path)

    assert conn.events[:4] == [
        "begin",
        f"lock:{(AGENCY_RUN_REQUEST_LOCK_KEY, 1)}",
        "in_flight_check",
        "insert",
    ]


@pytest.mark.parametrize(("path", "kind", "action", "chained"), _ROUTES)
def test_a_disabled_agency_is_refused(queued, path, kind, action, chained):
    conn = _Conn(deleted_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    response = _client(conn).post(path)

    assert response.status_code == 409
    assert queued == []


@pytest.mark.parametrize(
    ("static_url", "static_strategy"),
    [(None, "direct_url"), ("https://example.test/gtfs.zip", None)],
)
@pytest.mark.parametrize(("path", "kind", "action", "chained"), _ROUTES)
def test_an_agency_with_no_timetable_source_is_refused_before_anything_is_queued(
    queued, path, kind, action, chained, static_url, static_strategy
):
    """`refresh_static` treats a missing URL or strategy as "nothing to do"
    and returns quietly; answering 202 would report a reload that cannot
    happen."""
    conn = _Conn(static_url=static_url, static_strategy=static_strategy)
    response = _client(conn).post(path)

    assert response.status_code == 409
    assert "timetable" in response.json()["detail"]
    assert conn.inserted == []
    assert queued == []


@pytest.mark.parametrize(("path", "kind", "action", "chained"), _ROUTES)
def test_a_run_that_cannot_be_recorded_is_refused_rather_than_run_blind(queued, path, kind, action, chained):
    conn = _Conn(insert_unrecordable=True)
    response = _client(conn).post(path)

    assert response.status_code == 503
    assert queued == []


def test_reanalyze_is_refused_while_a_run_is_in_flight(queued):
    """Otherwise 再集計 pressed during a chained update's timetable step takes
    the ingest/analyze lock first and turns the chain's own sweep away."""
    conn = _Conn(in_flight=True)
    response = _client(conn).post("/api/admin/agencies/1/reanalyze")

    assert response.status_code == 409
    assert queued == []


def test_reanalyze_needs_no_timetable_source(queued):
    conn = _Conn(static_url=None, static_strategy=None)
    response = _client(conn).post("/api/admin/agencies/1/reanalyze")

    assert response.status_code == 202
    assert [call["reanalyze"]["run_id"] for call in queued] == [77]


@pytest.mark.parametrize(
    ("path", "runner"),
    [
        ("/api/admin/agencies/1/refresh-static", "_run_agency_update"),
        ("/api/admin/agencies/1/update", "_run_agency_update"),
        ("/api/admin/agencies/1/reanalyze", "_run_ingest_and_analyze"),
    ],
)
def test_the_connection_is_back_in_the_pool_before_the_job_runs(monkeypatch, path, runner):
    """A `Depends(get_conn)` connection is released only after the
    response's background tasks finish, so taking one here would keep it
    checked out for the whole fetch + ingest + analyze."""
    conn = _Conn()
    pool = CountingPool(conn)
    held_when_run: list[int] = []
    monkeypatch.setattr(f"api.routers.internal.{runner}", lambda **_kw: held_when_run.append(pool.held))

    response = _client(conn, pool).post(path)
    assert response.status_code == 202, response.text
    assert held_when_run == [0]
    assert pool.peak == 1
