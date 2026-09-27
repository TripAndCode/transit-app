"""A CSRF + admin-role sweep over every route the app actually mounts.

Routes are read off ``api.main.app`` itself, not a hand-maintained list, so a
new router -- admin or not -- is swept the moment it is mounted: a mutating
route that forgets its own ``csrf_guard(request)`` call, or an ``/api/admin``
route that forgets ``Depends(require_admin)``, fails loudly instead of
silently shipping an action any cross-site page, or any signed-in user, could
trigger.

Requests are served by a minimal standalone app assembled from every router
under ``api.routers``, with ``get_conn``/``get_ch`` overridden to stand-ins
that raise if ever touched -- a guard that let a request through would
otherwise fail with a confusing downstream error instead of naming the
missing guard. Each response also names the endpoint that served it, and the
sweeps require that to be the mounted route under test: a route the
standalone app does not reproduce, or whose filled-in path lands on a
neighbouring route, cannot pass on some other handler's guard.
"""

from __future__ import annotations

import importlib
import pkgutil
import re
import types
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Union, get_args, get_origin

import httpx
import pytest
from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.routing import APIRoute, iter_route_contexts
from fastapi.testclient import TestClient
from pydantic import BaseModel

import api.routers
from api import main
from api.deps import get_agency, get_ch, get_conn
from api.middleware.ratelimit import limiter
from api.security import User, csrf_guard, require_admin
from pipeline import flags
from tests.conftest import TEST_ORIGIN

_ADMIN = User(
    user_id=1,
    email="admin@example.com",
    name="Admin",
    avatar_url=None,
    role="admin",
    suspended_at=None,
    llm_approved=True,
)

_NON_ADMIN = User(
    user_id=2,
    email="member@example.com",
    name="Member",
    avatar_url=None,
    role="user",
    suspended_at=None,
    llm_approved=True,
)

_MUTATING_METHODS = {"POST", "PATCH", "PUT", "DELETE"}

_ADMIN_PREFIX = "/api/admin"

#: Mutating routes that deliberately make no ``csrf_guard`` call. CSRF abuses
#: a credential the browser attaches on its own (the session cookie); these
#: read no session and authenticate only by a shared secret in a request
#: header, which a cross-site page cannot supply. Every entry must name a
#: mounted route and that route must reject a request lacking its secret.
_CSRF_EXEMPT = frozenset(
    {
        # Scheduler trigger for the fallback ingest; X-Cron-Secret only.
        ("POST", "/internal/cron/ingest"),
        # Oracle collector push; X-Collector-Secret only.
        ("POST", "/internal/collector/updates/{agency_id}"),
    }
)

#: Routes discovery must always find, one per shape the sweeps rely on, so a
#: walk that silently stopped reaching a class of route fails here first.
_SENTINEL_ROUTES = frozenset(
    {
        ("PATCH", "/api/admin/flags/{key}"),
        ("GET", "/api/admin/users"),
        ("POST", "/api/me/presets"),
        ("POST", "/api/{agency_id}/ask"),
        ("POST", "/internal/cron/ingest"),
    }
)

_ENDPOINT_HEADER = "x-swept-endpoint"


@dataclass(frozen=True)
class _Route:
    method: str
    path: str
    endpoint: Callable[..., Any]
    body: dict[str, Any] | None


class _UnusedDependency:
    """Stands in for a DB/ClickHouse dependency that a rejected request must
    never reach. Raises only on actual use, so building the fake itself
    never fails -- only a guard that let the request through does, and with
    a message naming the gap instead of an opaque AttributeError."""

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(
            f".{name} was accessed -- the request should have been rejected by "
            "require_admin/csrf_guard before the handler touched a dependency"
        )


async def _any_agency(agency_id: int) -> int:
    """Accept any path agency without the pool connection ``get_agency`` opens."""
    return agency_id


@pytest.fixture(autouse=True)
def _every_flag_on_without_a_database(monkeypatch):
    """Resolve every registered kill switch to on, from env alone.

    A router-level dependency can gate a whole surface on a flag (the debug
    router 404s while its flag is off), which would answer before the
    handler's own guard ran; these sweeps are about the guard, not the switch.
    """
    monkeypatch.setattr(flags, "_load_overrides", lambda: {})
    for definition in flags.REGISTRY:
        monkeypatch.setenv(definition.env_var, "true")


@pytest.fixture(autouse=True)
def _fresh_rate_limits():
    """The limiter's buckets are process-wide: start from empty ones, and do
    not leave this sweep's requests counted against a later test."""
    limiter.reset()
    yield
    limiter.reset()


def _qualified_name(endpoint: Any) -> str:
    if endpoint is None:
        return "<no route matched>"
    return f"{endpoint.__module__}.{endpoint.__qualname__}"


def _routers() -> list[APIRouter]:
    """Every router object defined anywhere under ``api.routers``."""
    found: dict[int, APIRouter] = {}
    for module_info in pkgutil.iter_modules(api.routers.__path__, f"{api.routers.__name__}."):
        module = importlib.import_module(module_info.name)
        for value in vars(module).values():
            if isinstance(value, APIRouter):
                found[id(value)] = value
    return list(found.values())


def _build_app(user: User) -> FastAPI:
    app = FastAPI()
    for router in _routers():
        app.include_router(router)
    app.dependency_overrides[get_conn] = lambda: _UnusedDependency()
    app.dependency_overrides[get_ch] = lambda: _UnusedDependency()
    app.dependency_overrides[get_agency] = _any_agency

    @app.middleware("http")
    async def _as_user_reporting_the_endpoint(request, call_next):
        request.state.user = user
        response = await call_next(request)
        response.headers[_ENDPOINT_HEADER] = _qualified_name(request.scope.get("endpoint"))
        return response

    return app


def _concrete_path(path: str) -> str:
    """Fill every ``{param}`` placeholder with a value valid for any path
    param type the routers declare (all are ``int`` or plain ``str``)."""
    return re.sub(r"\{[^{}]+\}", "1", path)


def _is_optional_origin(origin: Any) -> bool:
    return origin is Union or origin is types.UnionType


def _dummy_value(annotation: Any) -> Any:
    """Best-effort dummy for a required Pydantic field, from its annotation
    alone -- just enough to pass body *validation* so a route's own
    ``csrf_guard(request)``/``require_admin`` check (not this filler) is
    what the sweeps below actually exercise."""
    origin = get_origin(annotation)
    args = get_args(annotation)
    if _is_optional_origin(origin):
        non_none = [a for a in args if a is not type(None)]
        return _dummy_value(non_none[0]) if non_none else None
    if annotation is str:
        return "x"
    if annotation in (int, float):
        return 1
    if annotation is bool:
        return True
    if origin in (list, set, frozenset):
        inner = _dummy_value(args[0]) if args else "x"
        return [inner]
    if annotation is dict or origin is dict:
        return {}
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return _dummy_body(annotation)
    return None


def _dummy_body(model_cls: type[BaseModel]) -> dict[str, Any]:
    """A dict with every *required* field of *model_cls* filled in, so a
    request reaches the handler's own guards instead of 422ing on body
    shape first. Fields with a default are left out entirely."""
    return {
        name: _dummy_value(field.annotation) for name, field in model_cls.model_fields.items() if field.is_required()
    }


def _body_for_route(route: APIRoute) -> dict[str, Any] | None:
    if route.body_field is None:
        return None
    return _dummy_body(route.body_field.field_info.annotation)


def _mounted_routes() -> list[_Route]:
    """Every (method, route) pair ``api.main.app`` serves.

    Through ``iter_route_contexts`` rather than a plain walk of
    ``app.routes``: this FastAPI keeps each included router there as one
    opaque entry, so a plain walk sees only the few routes declared on the
    app itself and every sweep built on it would pass having tested nearly
    nothing.
    """
    found: list[_Route] = []
    for context in iter_route_contexts(main.app.routes):
        route = context.original_route
        if not isinstance(route, APIRoute) or context.path is None or context.endpoint is None:
            continue
        body = _body_for_route(route)
        for method in sorted(context.methods or ()):
            found.append(_Route(method, context.path, context.endpoint, body))
    return found


def _csrf_swept_routes() -> list[_Route]:
    return [r for r in _mounted_routes() if r.method in _MUTATING_METHODS and (r.method, r.path) not in _CSRF_EXEMPT]


def _admin_routes() -> list[_Route]:
    return [r for r in _mounted_routes() if r.path == _ADMIN_PREFIX or r.path.startswith(f"{_ADMIN_PREFIX}/")]


def _rejection(guard: Callable[[Request], Any], *, user: User | None = None) -> tuple[int, Any]:
    """The (status, detail) *guard* itself raises for a bare POST with no
    Origin/Referer made as *user* -- the answer a swept route must give when
    that guard, and nothing else, is what stopped the request."""
    scope = {"type": "http", "method": "POST", "path": "/", "headers": [], "query_string": b"", "state": {"user": user}}
    with pytest.raises(HTTPException) as exc:
        guard(Request(scope))
    return exc.value.status_code, exc.value.detail


def _outcome(response: httpx.Response) -> tuple[int, Any, str | None]:
    try:
        payload = response.json()
    except ValueError:
        payload = response.text
    detail = payload.get("detail") if isinstance(payload, dict) else payload
    return response.status_code, detail, response.headers.get(_ENDPOINT_HEADER)


def test_route_discovery_finds_the_mounted_routes_the_sweeps_depend_on():
    """Canary on discovery: were it ever to come back empty or short, every
    sweep below would pass having tested nothing."""
    found = {(r.method, r.path) for r in _mounted_routes()}
    assert _SENTINEL_ROUTES <= found, sorted(_SENTINEL_ROUTES - found)
    assert len(_csrf_swept_routes()) >= 30, _csrf_swept_routes()
    assert len(_admin_routes()) >= 25, _admin_routes()


def test_every_csrf_exemption_names_a_mounted_mutating_route():
    mounted = {(r.method, r.path) for r in _mounted_routes() if r.method in _MUTATING_METHODS}
    stale = sorted(_CSRF_EXEMPT - mounted)
    assert stale == [], f"these CSRF exemptions match no mounted mutating route; remove them: {stale}"


def test_every_csrf_exempt_route_rejects_a_request_without_its_secret():
    """The exemption rests on the shared-secret check: 401 without the
    header, or 503 while no secret is configured, both fail closed."""
    client = TestClient(_build_app(_ADMIN), raise_server_exceptions=False)
    exempt = [r for r in _mounted_routes() if (r.method, r.path) in _CSRF_EXEMPT]
    assert exempt, "no mounted route matched a CSRF exemption"

    failures = []
    for route in exempt:
        response = client.request(route.method, _concrete_path(route.path))
        if response.status_code not in (401, 503):
            failures.append((route.method, route.path, _outcome(response)))
    assert failures == [], f"these CSRF-exempt routes did not reject a request lacking their secret: {failures}"


def test_every_mutating_route_rejects_a_request_with_no_valid_origin():
    # As an admin, so every role check passes and csrf_guard is the only
    # thing left that can stop the request.
    client = TestClient(_build_app(_ADMIN), raise_server_exceptions=False)
    status, detail = _rejection(csrf_guard)

    failures = []
    for route in _csrf_swept_routes():
        response = client.request(route.method, _concrete_path(route.path), json=route.body)
        outcome = _outcome(response)
        if outcome != (status, detail, _qualified_name(route.endpoint)):
            failures.append((route.method, route.path, outcome))
    assert failures == [], (
        "these mutating routes did not reject a request with no Origin/Referer "
        f"(expected {status} {detail!r} from csrf_guard in the route's own handler): {failures}"
    )


def test_every_admin_route_rejects_an_authenticated_non_admin():
    client = TestClient(_build_app(_NON_ADMIN), raise_server_exceptions=False)
    status, detail = _rejection(require_admin, user=_NON_ADMIN)

    failures = []
    for route in _admin_routes():
        # A valid Origin so a failure here is attributable to the role
        # check, not to csrf_guard rejecting the request first.
        response = client.request(
            route.method, _concrete_path(route.path), json=route.body, headers={"origin": TEST_ORIGIN}
        )
        outcome = _outcome(response)
        if outcome != (status, detail, _qualified_name(route.endpoint)):
            failures.append((route.method, route.path, outcome))
    assert failures == [], (
        f"these admin routes let a non-admin caller through (expected {status} {detail!r} "
        f"from require_admin): {failures}"
    )
