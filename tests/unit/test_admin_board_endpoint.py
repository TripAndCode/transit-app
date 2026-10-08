"""`GET /api/admin/board` wiring: every sub-check degrades on its own.

Exercised through a minimal standalone app (not ``api.main``) with
``require_admin``/``get_conn`` overridden, so nothing here touches a real
pool, database, or collector subprocess — the same pattern as
``tests/unit/test_admin_bounds.py``.
"""

from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import pipeline.health as health_mod
from api.admin_board import BOARD_WINDOW_DAYS, COLLECTOR_ORDER
from api.deps import get_ch, get_conn
from api.routers import admin as admin_router
from api.security import require_admin
from pipeline.health import AgencyFreshness
from tests.fixtures.users import admin_user

_ADMIN = admin_user()

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

_RUN_ROWS = [
    {
        "run_id": 1,
        "kind": "analyze",
        "agency_id": 1,
        "agency_name": "Hokuriku",
        "started_at": _NOW,
        "finished_at": _NOW,
        "status": "ok",
        "rows": 10,
        "lock_wait_ms": None,
        "error": None,
        "requested_by": None,
    }
]


class _Conn:
    """Fake asyncpg connection answering each of the board's queries by shape."""

    def __init__(
        self, *, approvals=0, join_error=None, agency_error=None, fetchval_error=None, acks=(), acks_error=None
    ):
        self.approvals = approvals
        self.acks = list(acks)
        self.acks_error = acks_error
        self.join_error = join_error
        self.agency_error = agency_error
        self.fetchval_error = fetchval_error
        self.freshness_args: tuple = ()

    async def fetch(self, sql, *args):
        if "admin_alert_acks" in sql:
            if self.acks_error is not None:
                raise self.acks_error
            return [{"alert_key": key} for key in self.acks if key in args[0]]
        if "schema_migrations" in sql:
            return [{"version": "0001"}]
        if "pipeline_runs" in sql:
            return _RUN_ROWS
        if "agg_feed_health" in sql:
            self.freshness_args = args
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
    app.dependency_overrides[get_ch] = lambda: None
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


def _stub_freshness(result):
    async def _freshness(conn, ch):
        if isinstance(result, Exception):
            raise result
        return result

    return _freshness


def _lagging(name: str, days: int) -> AgencyFreshness:
    return AgencyFreshness(
        agency_id=1,
        agency_name=name,
        last_analyzed_at=_NOW,
        analyze_age_hours=1.0,
        agg_fresh=False,
        agg_behind_days=days,
        is_stale=True,
        data_to=_YESTERDAY.isoformat(),
        clamp_pct=None,
    )


@pytest.fixture(autouse=True)
def _no_real_freshness_check(monkeypatch):
    """The Ops freshness check reads ClickHouse; a unit test answers for it,
    and starts with no answer cached from another test."""
    monkeypatch.setattr(health_mod, "aggregate_freshness", _stub_freshness([]))
    monkeypatch.setattr(admin_router, "_board_freshness_cache", None)


def _counting_freshness(result):
    calls: list[int] = []

    async def _freshness(conn, ch):
        calls.append(1)
        if isinstance(result, Exception):
            raise result
        return result

    return _freshness, calls


@pytest.fixture(autouse=True)
def reaped(monkeypatch) -> list[str | None]:
    """Capture the board's abandoned-run sweep rather than opening a
    connection to whatever `DATABASE_URL` happens to name."""
    calls: list[str | None] = []
    monkeypatch.setattr(admin_router, "_last_reap_at", None, raising=False)
    monkeypatch.setattr(
        admin_router,
        "reap_abandoned_runs_best_effort",
        lambda db_url: calls.append(db_url) or 0,
        raising=False,
    )
    return calls


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
            # Read at call time, not module import: the endpoint builds the
            # 24-hour history against its own `now`, so a success pinned to
            # import time drops a cell once the suite crosses an hour
            # boundary before reaching this test.
            "last_success_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
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


def test_every_alert_carries_its_key_and_whether_an_operator_acknowledged_it():
    from api.admin_board import alert_key

    first = {a["code"]: a for a in _client(_Conn(approvals=2)).get("/api/admin/board").json()["alerts"]}
    pending = first["llm_approvals_pending"]
    assert pending["key"] == alert_key(pending)
    assert pending["acked"] is False
    again = {
        a["code"]: a
        for a in _client(_Conn(approvals=2, acks=[pending["key"]])).get("/api/admin/board").json()["alerts"]
    }
    assert again["llm_approvals_pending"]["acked"] is True


def test_an_unreadable_acknowledgement_table_leaves_every_alert_unacknowledged():
    body = _client(_Conn(approvals=2, acks_error=RuntimeError("no table"))).get("/api/admin/board").json()
    assert body["alerts"]
    assert all(a["acked"] is False for a in body["alerts"])


def test_staleness_alerts_come_from_the_check_ops_reads(monkeypatch):
    monkeypatch.setattr(health_mod, "aggregate_freshness", _stub_freshness([_lagging("Hokuriku", 2)]))
    alerts = {a["code"]: a for a in _client(_Conn()).get("/api/admin/board").json()["alerts"]}
    assert alerts["agency_stale"]["params"] == {"agency": "Hokuriku", "days": 2}


def test_a_failing_freshness_check_raises_no_staleness_alert(monkeypatch):
    monkeypatch.setattr(health_mod, "aggregate_freshness", _stub_freshness(RuntimeError("clickhouse down")))
    response = _client(_Conn()).get("/api/admin/board")
    assert response.status_code == 200
    assert not {"agency_stale", "agencies_stale", "agencies_never_analyzed"} & {
        a["code"] for a in response.json()["alerts"]
    }


@pytest.mark.parametrize("result", [[], RuntimeError("clickhouse down")])
def test_a_polled_board_reuses_one_freshness_answer(monkeypatch, result):
    """Staleness compares whole days; the board polls every few seconds."""
    freshness, calls = _counting_freshness(result)
    monkeypatch.setattr(health_mod, "aggregate_freshness", freshness)
    client = _client(_Conn())
    client.get("/api/admin/board")
    client.get("/api/admin/board")
    assert len(calls) == 1


async def test_polls_that_arrive_together_share_one_freshness_check(monkeypatch):
    release = asyncio.Event()
    calls: list[int] = []

    async def _slow_freshness(conn, ch):
        calls.append(1)
        await release.wait()
        return []

    monkeypatch.setattr(health_mod, "aggregate_freshness", _slow_freshness)
    first = asyncio.create_task(admin_router._board_agency_freshness(None, None))
    await asyncio.sleep(0)
    try:
        # A second probe would wait on `release` too, so a short timeout is the
        # failure signal rather than a hang.
        second = await asyncio.wait_for(admin_router._board_agency_freshness(None, None), timeout=1.0)
    finally:
        release.set()
    assert await first == []
    assert second is None
    assert len(calls) == 1


def test_an_expired_freshness_answer_is_checked_again(monkeypatch):
    freshness, calls = _counting_freshness([])
    monkeypatch.setattr(health_mod, "aggregate_freshness", freshness)
    monkeypatch.setattr(admin_router, "_BOARD_FRESHNESS_TTL_SEC", 0.0)
    client = _client(_Conn())
    client.get("/api/admin/board")
    client.get("/api/admin/board")
    assert len(calls) == 2


def test_clamped_samples_above_the_threshold_surface_as_a_warn_alert():
    alerts = {a["code"]: a for a in _client(_Conn()).get("/api/admin/board").json()["alerts"]}
    assert alerts["clamp_high"]["level"] == "warn"


#: How long a poll may take before the test calls it hung. Only a
#: regression -- a poll that waits out the wedged collector -- ever runs into
#: it; a passing poll gives up on its own much shorter budget.
_HANG_GUARD_SECONDS = 10.0


async def test_collectors_are_abandoned_once_the_budget_expires(monkeypatch):
    """A wedged collector costs the endpoint its budget, not the request."""
    release = threading.Event()

    def _wedged():
        release.wait()
        return ["never"]

    monkeypatch.setattr(admin_router, "_COLLECTOR_BUDGET_SECONDS", 0.05)
    monkeypatch.setattr(admin_router, "_collect_all", _wedged)
    admin_router._collector_task = None
    try:
        assert await asyncio.wait_for(_REAL_COLLECT_DOCUMENTS(), _HANG_GUARD_SECONDS) == []
        abandoned = admin_router._collector_task
        assert abandoned is not None
        assert not abandoned.done()  # given up on, not finished
    finally:
        release.set()
        if admin_router._collector_task is not None:
            await asyncio.wait([admin_router._collector_task])
        admin_router._collector_task = None


async def test_only_one_collection_runs_however_many_polls_arrive(monkeypatch):
    """The default executor is shared with the embedder and the LLM calls
    and holds only a handful of threads, so a board left open in several
    tabs must not spend them all on abandoned collections."""
    started = 0
    lock = threading.Lock()
    release = threading.Event()

    def _wedged():
        nonlocal started
        with lock:
            started += 1
        release.wait()
        return []

    monkeypatch.setattr(admin_router, "_COLLECTOR_BUDGET_SECONDS", 0.05)
    monkeypatch.setattr(admin_router, "_collect_all", _wedged)
    admin_router._collector_task = None
    try:
        polls = asyncio.gather(*(_REAL_COLLECT_DOCUMENTS() for _ in range(6)))
        results = await asyncio.wait_for(polls, _HANG_GUARD_SECONDS)
        assert results == [[]] * 6  # every poll gave up on its own budget

        shared = admin_router._collector_task
        assert shared is not None
        assert not shared.done()  # the abandoned collection is still the one running
        release.set()
        # Waits for every collection thread, not just the shared one, so a
        # second collection -- had one been started -- is certain to be counted.
        await asyncio.get_running_loop().shutdown_default_executor()
        await shared
        assert started == 1
    finally:
        release.set()
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


def test_the_freshness_join_is_bounded_at_both_ends_of_the_window():
    """Unbounded at the top, the join drags in every future-dated feed-health
    row — days the heatmap never draws — on every poll."""
    conn = _Conn()
    _client(conn).get("/api/admin/board")
    window_start, window_end = conn.freshness_args
    assert window_end - window_start == timedelta(days=BOARD_WINDOW_DAYS)
    assert "h.date >= $1" in admin_router._BOARD_FRESHNESS_SQL
    assert "h.date < $2" in admin_router._BOARD_FRESHNESS_SQL


def test_the_board_reaps_runs_nothing_will_ever_close(reaped):
    _client(_Conn()).get("/api/admin/board")
    assert len(reaped) == 1


def test_a_polled_board_does_not_pay_for_the_reap_on_every_request(reaped):
    """Operators poll this page every few seconds; a run only becomes
    reapable after hours."""
    client = _client(_Conn())
    client.get("/api/admin/board")
    client.get("/api/admin/board")
    client.get("/api/admin/board")
    assert len(reaped) == 1


def test_a_failing_reap_never_costs_the_board_its_answer(monkeypatch, reaped):
    def _boom(_db_url):
        raise RuntimeError("pipeline_runs is unreadable")

    monkeypatch.setattr(admin_router, "reap_abandoned_runs_best_effort", _boom)
    assert _client(_Conn()).get("/api/admin/board").status_code == 200
