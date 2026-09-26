"""A CSRF + role sweep over every mutating route under ``/api/admin``.

Walks the actual router objects (not a hand-maintained list) so a new
mutating admin route is covered automatically: if it forgets
``Depends(require_admin)`` or its own ``csrf_guard(request)`` call, this
fails loudly instead of silently shipping an admin action any authenticated
user, or any cross-site page, could trigger.

Exercised through a minimal standalone app assembled from the same router
objects ``api.main`` mounts, with ``get_conn``/``get_ch`` overridden to
stand-ins that raise if ever touched -- a guard that let a request through
would otherwise fail with a confusing downstream AttributeError instead of
naming the missing guard.
"""

from __future__ import annotations

import re
import types
from typing import Any, Union, get_args, get_origin

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from api.deps import get_ch, get_conn
from api.routers import admin, admin_agencies, admin_ask, admin_flags
from api.security import User, require_admin
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

_ADMIN_ROUTERS = (admin_agencies.router, admin.router, admin_ask.router, admin_flags.router)


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


def _build_app() -> FastAPI:
    app = FastAPI()
    for router in _ADMIN_ROUTERS:
        app.include_router(router)
    app.dependency_overrides[get_conn] = lambda: _UnusedDependency()
    app.dependency_overrides[get_ch] = lambda: _UnusedDependency()
    return app


def _concrete_path(path: str) -> str:
    """Fill every ``{param}`` placeholder with a value valid for any of this
    router set's path param types (all are ``int`` or plain ``str``)."""
    return re.sub(r"\{[^{}]+\}", "1", path)


def _is_optional_origin(origin: Any) -> bool:
    return origin is Union or origin is types.UnionType


def _dummy_value(annotation: Any) -> Any:
    """Best-effort dummy for a required Pydantic field, from its annotation
    alone -- just enough to pass body *validation* so a route's own
    ``csrf_guard(request)``/``require_admin`` check (not this filler) is
    what the sweep below actually exercises."""
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


def _body_for_route(route: Any) -> dict[str, Any] | None:
    body_field = getattr(route, "body_field", None)
    if body_field is None:
        return None
    return _dummy_body(body_field.field_info.annotation)


def _mutating_admin_routes() -> list[tuple[str, str, dict[str, Any] | None]]:
    """Every mutating `/api/admin` route, read off the routers themselves.

    Not off a composed `app.routes`: FastAPI keeps an included router as one
    opaque `_IncludedRouter` entry there rather than flattening its routes in,
    so that walk finds no paths at all and every sweep built on it passes
    having tested nothing. The routers already carry their own prefix, so
    their `path` is the full one either way -- and reading them directly also
    means this cannot drift with how the app happens to be composed.
    """
    found: list[tuple[str, str, dict[str, Any] | None]] = []
    for router in _ADMIN_ROUTERS:
        for route in router.routes:
            path = getattr(route, "path", "")
            if not path.startswith("/api/admin"):
                continue
            methods = getattr(route, "methods", None) or set()
            if not (methods & _MUTATING_METHODS):
                continue
            body = _body_for_route(route)
            for method in sorted(methods & _MUTATING_METHODS):
                found.append((method, path, body))
    return found


def test_the_route_walk_itself_finds_every_known_mutating_admin_route():
    """Canary on the walker: if this drops to zero (or far below what the
    routers currently declare), the sweeps below would silently pass having
    tested nothing.

    It has already earned its place once -- the first version of the walk read
    `app.routes`, which under this FastAPI holds one opaque entry per included
    router and no paths, so both sweeps were vacuous and only this assertion
    said so.
    """
    routes = _mutating_admin_routes()
    assert len(routes) >= 14, routes


def test_every_mutating_admin_route_rejects_a_request_with_no_valid_origin():
    app = _build_app()
    app.dependency_overrides[require_admin] = lambda: _ADMIN
    client = TestClient(app, raise_server_exceptions=False)

    failures = []
    for method, path, body in _mutating_admin_routes():
        response = client.request(method, _concrete_path(path), json=body if body is not None else {})
        if response.status_code != 403:
            failures.append((method, path, response.status_code, response.text))
    assert failures == [], (
        "these mutating admin routes did not reject a request with no Origin/Referer "
        f"(expected 403 from csrf_guard): {failures}"
    )


def test_every_mutating_admin_route_rejects_an_authenticated_non_admin():
    app = _build_app()

    @app.middleware("http")
    async def _inject_non_admin_user(request, call_next):
        request.state.user = _NON_ADMIN
        return await call_next(request)

    client = TestClient(app, raise_server_exceptions=False)

    failures = []
    for method, path, body in _mutating_admin_routes():
        # A valid Origin so a failure here is attributable to the role
        # check, not to csrf_guard rejecting the request first.
        response = client.request(
            method, _concrete_path(path), json=body if body is not None else {}, headers={"origin": TEST_ORIGIN}
        )
        if response.status_code != 403:
            failures.append((method, path, response.status_code, response.text))
    assert failures == [], (
        f"these mutating admin routes let a non-admin caller through (expected 403 from require_admin): {failures}"
    )
