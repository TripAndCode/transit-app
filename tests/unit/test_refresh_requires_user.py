"""POST /api/{agency_id}/delays/refresh fetches the agency's live feed and
writes it: a signed-in action, metered per account rather than per address.

Exercised through a standalone app with `get_agency`/`require_user`
overridden and the ingest stubbed, so no database or feed is touched."""

from __future__ import annotations

from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

import api.routers.map as map_mod
from api.deps import get_agency
from api.middleware.ratelimit import limiter, user_key
from api.security import require_user
from tests.fixtures.users import admin_user


def _client(*, signed_in: bool) -> TestClient:
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]
    app.include_router(map_mod.router)
    app.dependency_overrides[get_agency] = lambda: 7
    if signed_in:
        app.dependency_overrides[require_user] = lambda: admin_user()
    return TestClient(app)


def test_an_anonymous_refresh_is_refused_before_any_feed_fetch(monkeypatch):
    monkeypatch.setenv("ALLOW_TEST_ORIGIN", "1")
    fetched: list[int] = []
    monkeypatch.setattr(map_mod, "_ingest_live_agency", lambda agency_id: fetched.append(agency_id) or 1)
    resp = _client(signed_in=False).post("/api/7/delays/refresh", headers={"Origin": "http://test"})
    assert resp.status_code == 401
    assert fetched == []


def test_a_signed_in_refresh_runs_the_ingest(monkeypatch):
    monkeypatch.setenv("ALLOW_TEST_ORIGIN", "1")
    limiter.reset()
    monkeypatch.setattr(map_mod, "_ingest_live_agency", lambda agency_id: 3)
    resp = _client(signed_in=True).post("/api/7/delays/refresh", headers={"Origin": "http://test"})
    assert resp.status_code == 200
    assert resp.json() == {"status": "updated", "inserted": 3}


def test_the_refresh_limit_is_keyed_on_the_account():
    request = MagicMock()
    request.state.user = admin_user(42)
    assert user_key(request) == "user:42"


def test_user_key_falls_back_to_the_address_without_a_user():
    request = MagicMock()
    request.state.user = None
    request.client.host = "203.0.113.5"
    assert user_key(request) == "203.0.113.5"


def test_refresh_is_declared_behind_require_user():
    route = next(r for r in map_mod.router.routes if isinstance(r, APIRoute) and r.path.endswith("/delays/refresh"))
    assert any(d.call is require_user for d in route.dependant.dependencies)
