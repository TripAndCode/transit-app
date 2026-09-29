"""Which routes a signed-out caller may reach while sign-in is required.

The gate is deny-by-default, so this list is where a decision to make a route
public is recorded: adding one here is a review-visible change, and a route
that turns public by accident fails here first.
"""

from fastapi.routing import iter_route_contexts

from api.main import app
from api.middleware.login_gate import needs_login

PUBLIC_ROUTES = {
    "/api/auth/local/login",
    "/api/auth/logout",
    "/api/auth/{provider}/callback",
    "/api/auth/{provider}/login",
    "/api/config",
    "/health",
    "/internal/collector/updates/{agency_id}",
    "/internal/cron/ingest",
}


def _served_paths() -> set[str]:
    # iter_route_contexts, not app.routes: this FastAPI keeps each included
    # router in app.routes as one opaque entry.
    return {c.path for c in iter_route_contexts(app.routes) if c.path}


def test_only_the_sign_in_flow_and_infrastructure_are_public():
    assert {p for p in _served_paths() if not needs_login(p)} == PUBLIC_ROUTES


def test_the_walk_sees_the_included_routers():
    assert len(_served_paths()) > 50


def test_openapi_docs_are_gated():
    assert all(needs_login(p) for p in ("/docs", "/redoc", "/openapi.json"))
