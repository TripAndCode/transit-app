"""The agency diagnostics endpoint's seven reads must run concurrently, each
on its own pooled connection -- a single asyncpg connection cannot multiplex
queries, so awaiting them one at a time serializes seven round trips that
have no dependency on each other -- and no connection may be held while
another is waited for, or enough concurrent requests drain the pool and wait
on each other forever.

Exercised through a minimal standalone app with a fake pool that records
every acquire and release.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routers import admin_agencies
from api.security import require_admin
from tests.fixtures.users import admin_user

_ADMIN = admin_user()

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


class _FakePoolConn:
    async def fetch(self, _sql, *_args):
        return []

    async def fetchrow(self, sql, *_args):
        return _AGENCY_ROW if "FROM agencies a" in sql else None


class _FakePool:
    def __init__(self):
        self.events: list[str] = []

    def acquire(self):
        @asynccontextmanager
        async def _cm():
            self.events.append("acquire")
            try:
                yield _FakePoolConn()
            finally:
                self.events.append("release")

        return _cm()


def _client(pool: _FakePool) -> TestClient:
    app = FastAPI()
    app.include_router(admin_agencies.router)
    app.state.pool = pool
    app.dependency_overrides[require_admin] = lambda: _ADMIN
    return TestClient(app)


def test_diagnostics_runs_the_seven_reads_on_their_own_pooled_connections():
    pool = _FakePool()
    r = _client(pool).get("/api/admin/agencies/1/diagnostics")
    assert r.status_code == 200
    # The header lookup, then the seven reads; no request-scoped connection.
    assert pool.events.count("acquire") == 8
    # The header's connection goes back before any read takes one.
    assert pool.events[:3] == ["acquire", "release", "acquire"]
    body = r.json()
    assert body["agency_id"] == 1
    assert body["standards"] == []
    assert body["weights"] == []
