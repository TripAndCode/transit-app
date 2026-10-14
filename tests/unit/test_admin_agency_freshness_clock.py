"""The admin freshness badge judges the newest aggregated day against the
service day in progress (:mod:`pipeline.service_day`), not the calendar day.

Aggregates are filed by service date, and the day in progress cannot be
aggregated yet, so the day before it is the newest one that can be. Between
midnight and the cutover that day in progress is still the previous date.

Exercised through a minimal standalone app over fake connections, both on the
agency list and in the agency drawer.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date, datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.range as range_mod
from api.deps import get_conn
from api.routers import admin_agencies
from api.security import require_admin
from tests.fixtures.users import admin_user

THURSDAY = date(2026, 3, 5)
SATURDAY_0030_JST = datetime(2026, 3, 6, 15, 30, tzinfo=timezone.utc)
SATURDAY_0430_JST = datetime(2026, 3, 6, 19, 30, tzinfo=timezone.utc)

_HEADER = {
    "agency_id": 1,
    "agency_name": "Hokuriku",
    "feed_url": "https://example.test/feed.pb",
    "static_url": None,
    "ingest_strategy": "direct_url",
    "deleted_at": None,
    "analyzed_at": None,
    "max_updates_captured_at": None,
    "latest_data_date": THURSDAY,
}


class _FakeConn:
    async def fetch(self, sql, *_args):
        return [_HEADER] if "FROM agencies a" in sql else []

    async def fetchrow(self, sql, *_args):
        return _HEADER if "FROM agencies a" in sql else None


class _FakePool:
    def acquire(self):
        @asynccontextmanager
        async def _cm():
            yield _FakeConn()

        return _cm()


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(admin_agencies.router)
    app.state.pool = _FakePool()
    app.dependency_overrides[require_admin] = admin_user
    app.dependency_overrides[get_conn] = _FakeConn
    return TestClient(app)


def _freeze(monkeypatch, moment: datetime) -> None:
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return moment.astimezone(tz) if tz else moment

    monkeypatch.setattr(range_mod, "datetime", FrozenDateTime)


@pytest.mark.parametrize(
    ("moment", "freshness", "service_day"),
    [(SATURDAY_0030_JST, "fresh", "2026-03-06"), (SATURDAY_0430_JST, "stale", "2026-03-07")],
)
def test_the_badge_turns_stale_at_the_cutover_not_at_midnight(monkeypatch, moment, freshness, service_day):
    """The newest aggregate is Thursday's. At 00:30 on Saturday Friday's service
    day is still running, so Thursday is as new as an aggregate can be; once
    Saturday's opens, Friday's should have been aggregated. The clamp-rate
    sparkline ends on the same service day."""
    _freeze(monkeypatch, moment)
    client = _client()

    [row] = client.get("/api/admin/agencies/health").json()
    drawer = client.get("/api/admin/agencies/1/diagnostics").json()

    assert (row["freshness"], drawer["freshness"]) == (freshness, freshness)
    assert (row["clamp_history"][-1]["date"], drawer["clamp_history"][-1]["date"]) == (service_day, service_day)
