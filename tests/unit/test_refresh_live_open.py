"""POST /api/{agency_id}/delays/refresh fetches the agency's live feed and
writes it. It is open to every visitor, signed in or not, under a per-caller
rate limit (per address, or per account when signed in) and a per-agency
cooldown that only a successful poll starts. An
agency with no timetable loaded is refused as unavailable, not as a failed
feed.

Exercised through a standalone app with `get_agency` overridden and the
ingest stubbed, so no database or feed is touched."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

import api.routers.map as map_mod
from api.deps import get_agency
from api.middleware.ratelimit import limiter, user_key
from api.sso import SSO_ENV
from pipeline.ingest import StaticTimetableMissing
from tests.fixtures.users import admin_user

_ORIGIN = {"Origin": "http://test"}


@pytest.fixture(autouse=True)
def _fresh_limits(monkeypatch):
    monkeypatch.setenv("ALLOW_TEST_ORIGIN", "1")
    for var in SSO_ENV:
        monkeypatch.setenv(var, "x")
    limiter.reset()
    map_mod._LAST_LIVE_POLL.clear()
    yield
    limiter.reset()
    map_mod._LAST_LIVE_POLL.clear()


def _client(*, signed_in: bool = False) -> TestClient:
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]
    app.include_router(map_mod.router)
    app.dependency_overrides[get_agency] = lambda: 7

    @app.middleware("http")
    async def _session(request, call_next):
        request.state.user = admin_user() if signed_in else None
        return await call_next(request)

    return TestClient(app)


def _stub_ingest(monkeypatch) -> list[int]:
    fetched: list[int] = []
    monkeypatch.setattr(map_mod, "_ingest_live_agency", lambda agency_id: fetched.append(agency_id) or 3)
    return fetched


def _refuse_with(monkeypatch, exc: Exception) -> list[int]:
    attempts: list[int] = []

    def _ingest(agency_id):
        attempts.append(agency_id)
        raise exc

    monkeypatch.setattr(map_mod, "_ingest_live_agency", _ingest)
    return attempts


def test_an_anonymous_refresh_runs_the_ingest_while_sign_in_exists(monkeypatch):
    fetched = _stub_ingest(monkeypatch)
    resp = _client().post("/api/7/delays/refresh", headers=_ORIGIN)
    assert resp.status_code == 200
    assert resp.json() == {"status": "updated", "inserted": 3}
    assert fetched == [7]


def test_a_signed_in_refresh_runs_the_ingest(monkeypatch):
    fetched = _stub_ingest(monkeypatch)
    assert _client(signed_in=True).post("/api/7/delays/refresh", headers=_ORIGIN).status_code == 200
    assert fetched == [7]


def test_a_refresh_inside_the_cooldown_answers_without_fetching(monkeypatch):
    fetched = _stub_ingest(monkeypatch)
    client = _client()
    assert client.post("/api/7/delays/refresh", headers=_ORIGIN).json()["status"] == "updated"
    resp = client.post("/api/7/delays/refresh", headers=_ORIGIN)
    assert resp.status_code == 200
    assert resp.json() == {"status": "recent", "inserted": 0}
    assert fetched == [7]


def test_the_cooldown_ends_after_its_window(monkeypatch):
    fetched = _stub_ingest(monkeypatch)
    client = _client()
    client.post("/api/7/delays/refresh", headers=_ORIGIN)
    map_mod._LAST_LIVE_POLL[7] -= map_mod.LIVE_POLL_COOLDOWN_S
    assert client.post("/api/7/delays/refresh", headers=_ORIGIN).json()["status"] == "updated"
    assert fetched == [7, 7]


def test_the_sixth_refresh_in_a_minute_is_rate_limited(monkeypatch):
    _stub_ingest(monkeypatch)
    client = _client()
    codes = [client.post("/api/7/delays/refresh", headers=_ORIGIN).status_code for _ in range(6)]
    assert codes == [200] * 5 + [429]


def test_a_failed_refresh_does_not_start_the_cooldown(monkeypatch):
    attempts = _refuse_with(monkeypatch, RuntimeError("feed down"))
    client = _client()
    assert client.post("/api/7/delays/refresh", headers=_ORIGIN).status_code == 502
    assert client.post("/api/7/delays/refresh", headers=_ORIGIN).status_code == 502
    assert attempts == [7, 7]
    assert 7 not in map_mod._LAST_LIVE_POLL


def test_a_refresh_refused_by_the_ingest_lock_does_not_start_the_cooldown(monkeypatch):
    attempts = _refuse_with(monkeypatch, HTTPException(status_code=409, detail="A data refresh is already in progress"))
    client = _client()
    assert client.post("/api/7/delays/refresh", headers=_ORIGIN).status_code == 409
    assert client.post("/api/7/delays/refresh", headers=_ORIGIN).status_code == 409
    assert attempts == [7, 7]
    assert 7 not in map_mod._LAST_LIVE_POLL


def test_a_refresh_for_an_agency_with_no_timetable_is_unavailable(monkeypatch):
    _refuse_with(monkeypatch, StaticTimetableMissing(7, "static_join"))
    resp = _client().post("/api/7/delays/refresh", headers=_ORIGIN)
    assert resp.status_code == 503
    assert 7 not in map_mod._LAST_LIVE_POLL


def test_the_refresh_limit_is_keyed_on_the_account_when_signed_in():
    request = MagicMock()
    request.state.user = admin_user(42)
    assert user_key(request) == "user:42"


def test_user_key_falls_back_to_the_address_without_a_user():
    request = MagicMock()
    request.state.user = None
    request.client.host = "203.0.113.5"
    assert user_key(request) == "203.0.113.5"
