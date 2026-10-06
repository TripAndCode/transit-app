"""POST /api/{agency_id}/delays/refresh fetches the agency's live feed and
writes it: a signed-in action while sign-in exists, metered per account
rather than per address; open under the per-address limit in anonymous-only
mode.

Exercised through a standalone app with `get_agency` overridden, the signed-in
user injected through request state, and the ingest stubbed, so no database or
feed is touched."""

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
from api.security import require_user_when_sign_in_exists
from api.sso import SSO_ENV
from tests.fixtures.users import admin_user


def _client(*, signed_in: bool) -> TestClient:
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


def _post(monkeypatch, *, signed_in: bool, sso: bool) -> tuple[int, list[int]]:
    monkeypatch.setenv("ALLOW_TEST_ORIGIN", "1")
    for var in SSO_ENV:
        if sso:
            monkeypatch.setenv(var, "x")
        else:
            monkeypatch.delenv(var, raising=False)
    limiter.reset()
    fetched: list[int] = []
    monkeypatch.setattr(map_mod, "_ingest_live_agency", lambda agency_id: fetched.append(agency_id) or 3)
    resp = _client(signed_in=signed_in).post("/api/7/delays/refresh", headers={"Origin": "http://test"})
    return resp.status_code, fetched


def test_an_anonymous_refresh_is_refused_before_any_feed_fetch_while_sign_in_exists(monkeypatch):
    assert _post(monkeypatch, signed_in=False, sso=True) == (401, [])


def test_a_signed_in_refresh_runs_the_ingest(monkeypatch):
    assert _post(monkeypatch, signed_in=True, sso=True) == (200, [7])


def test_anonymous_only_mode_keeps_the_refresh_open(monkeypatch):
    assert _post(monkeypatch, signed_in=False, sso=False) == (200, [7])


def test_the_refresh_limit_is_keyed_on_the_account():
    request = MagicMock()
    request.state.user = admin_user(42)
    assert user_key(request) == "user:42"


def test_user_key_falls_back_to_the_address_without_a_user():
    request = MagicMock()
    request.state.user = None
    request.client.host = "203.0.113.5"
    assert user_key(request) == "203.0.113.5"


def test_refresh_is_declared_behind_the_sign_in_dependency():
    route = next(r for r in map_mod.router.routes if isinstance(r, APIRoute) and r.path.endswith("/delays/refresh"))
    assert any(d.call is require_user_when_sign_in_exists for d in route.dependant.dependencies)
