"""The agency diagnostics endpoint's seven reads must run concurrently, each
on its own pooled connection -- a single asyncpg connection cannot multiplex
queries, so awaiting them one at a time on the request's shared ``conn``
serializes seven round trips that have no dependency on each other.

Exercised through a minimal standalone app with a fake pool that counts
``acquire()`` calls, and a request-scoped fake connection that raises if
anything but the one header lookup runs on it -- proving the seven reads
were moved off it.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

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

_AGENCY_ROW = {
    "agency_id": 1,
    "agency_name": "Hokuriku",
    "feed_url": "https://example.test/feed.pb",
    "static_url": None,
    "ingest_strategy": "direct_url",
    "deleted_at": None,
    "analyzed_at": None,
    "max_updates_captured_at": None,
    "latest_data_date": None,
}


class _HeaderOnlyConn:
    """The request-scoped connection: only the one header lookup may run on
    it. Anything else means a diagnostics read didn't move to the pool."""

    async def fetchrow(self, sql, *_args):
        if "FROM agencies a" in sql:
            return _AGENCY_ROW
        raise AssertionError(f"unexpected fetchrow on the request-scoped connection: {sql}")

    async def fetch(self, sql, *_args):
        raise AssertionError(f"unexpected fetch on the request-scoped connection: {sql}")


class _FakePoolConn:
    async def fetch(self, _sql, *_args):
        return []

    async def fetchrow(self, _sql, *_args):
        return None


class _FakePool:
    def __init__(self):
        self.acquire_count = 0

    def acquire(self):
        self.acquire_count += 1

        @asynccontextmanager
        async def _cm():
            yield _FakePoolConn()

        return _cm()


def _client(pool: _FakePool) -> TestClient:
    app = FastAPI()
    app.include_router(admin_agencies.router)
    app.state.pool = pool
    app.dependency_overrides[require_admin] = lambda: _ADMIN
    app.dependency_overrides[get_conn] = lambda: _HeaderOnlyConn()
    return TestClient(app)


def test_diagnostics_runs_the_seven_reads_on_their_own_pooled_connections():
    pool = _FakePool()
    r = _client(pool).get("/api/admin/agencies/1/diagnostics")
    assert r.status_code == 200
    assert pool.acquire_count == 7, (
        f"expected 7 pool.acquire() calls (one per independent read), got {pool.acquire_count}"
    )
    body = r.json()
    assert body["agency_id"] == 1
    assert body["standards"] == []
    assert body["weights"] == []
