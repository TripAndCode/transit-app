"""Every admin route carries its own ``require_admin`` dependency.

Nothing in front of the routers refuses a signed-out caller, so each admin
route's guard is the only thing between a reader and the admin surface.
This walks the app's real route table, included routers and their
router-level dependencies among them.
"""

from collections.abc import Callable, Iterator

from fastapi.dependencies.models import Dependant
from fastapi.routing import iter_route_contexts

from api.main import _require_docs_enabled, app
from api.security import require_admin

_ADMIN_PREFIX = "/api/admin"

# Admin-only routes that live outside the /api/admin namespace. Pinned so a
# new one is a review-visible decision rather than an accident.
ADMIN_ROUTES_OUTSIDE_THE_ADMIN_NAMESPACE = {
    ("POST", "/api/agencies"),
    ("PATCH", "/api/agencies/{agency_id}"),
    ("DELETE", "/api/agencies/{agency_id}"),
    ("POST", "/api/agencies/{agency_id}/restore"),
    ("POST", "/api/debug/perf/reset"),
    ("GET", "/api/ml/evaluations"),
}


def _calls(dependant: Dependant | None) -> Iterator[Callable]:
    if dependant is None:
        return
    for dep in dependant.dependencies:
        if dep.call is not None:
            yield dep.call
        yield from _calls(dep)


def _served_routes() -> dict[tuple[str, str], set[Callable]]:
    # iter_route_contexts, not app.routes: this FastAPI keeps each included
    # router in app.routes as one opaque entry, and the context's dependant
    # carries the dependencies the router and its inclusion add.
    routes: dict[tuple[str, str], set[Callable]] = {}
    for ctx in iter_route_contexts(app.routes):
        if not ctx.path:
            continue
        calls = set(_calls(getattr(ctx, "dependant", None)))
        for method in ctx.methods or ():
            if method != "HEAD":
                routes[(method, ctx.path)] = calls
    return routes


def _is_admin_path(path: str) -> bool:
    return path == _ADMIN_PREFIX or path.startswith(_ADMIN_PREFIX + "/")


def test_every_admin_route_requires_an_admin():
    admin = {route: calls for route, calls in _served_routes().items() if _is_admin_path(route[1])}
    assert len(admin) > 30, "the walk found too few admin routes to be looking at the real route table"
    unguarded = sorted(route for route, calls in admin.items() if require_admin not in calls)
    assert unguarded == []


def test_every_route_that_requires_an_admin_is_an_admin_route():
    outside = {
        route for route, calls in _served_routes().items() if require_admin in calls and not _is_admin_path(route[1])
    }
    assert outside == ADMIN_ROUTES_OUTSIDE_THE_ADMIN_NAMESPACE


def test_the_openapi_docs_stay_behind_their_switch():
    routes = _served_routes()
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert _require_docs_enabled in routes[("GET", path)], path
