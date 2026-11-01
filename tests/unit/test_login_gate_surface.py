"""Which routes a signed-out caller may reach while sign-in is required.

The gate is deny-by-default, so this list is where a decision to make a route
public is recorded: adding one here is a review-visible change, and a route
that turns public by accident fails here first.
"""

from fastapi.routing import iter_route_contexts

from api.main import app
from api.middleware.login_gate import needs_login

PUBLIC_ROUTES = {
    ("POST", "/api/auth/local/login"),
    ("POST", "/api/auth/logout"),
    ("GET", "/api/auth/{provider}/callback"),
    ("GET", "/api/auth/{provider}/login"),
    ("GET", "/api/config"),
    # Who-am-I answers a signed-out caller with null; it discloses nothing,
    # and the SPA asks it on every page, the landing page included.
    ("GET", "/api/me"),
    ("GET", "/health"),
    ("POST", "/internal/collector/updates/{agency_id}"),
    ("POST", "/internal/cron/ingest"),
}


def _served_routes() -> set[tuple[str, str]]:
    # iter_route_contexts, not app.routes: this FastAPI keeps each included
    # router in app.routes as one opaque entry.
    return {(m, c.path) for c in iter_route_contexts(app.routes) if c.path for m in (c.methods or ())}


def _served_paths() -> set[str]:
    return {path for _, path in _served_routes()}


def test_only_the_sign_in_flow_and_infrastructure_are_public():
    public = {(m, p) for m, p in _served_routes() if not needs_login(p, m)}
    assert {(m, p) for m, p in public if m != "HEAD"} == PUBLIC_ROUTES


def test_only_reading_who_am_i_is_public_under_api_me():
    assert not needs_login("/api/me", "GET")
    assert not needs_login("/api/me", "HEAD")
    for method in ("DELETE", "POST", "PUT", "PATCH"):
        assert needs_login("/api/me", method)
    assert needs_login("/api/me/sessions", "GET")
    assert needs_login("/api/me/export", "GET")


def test_the_walk_sees_the_included_routers():
    assert len(_served_paths()) > 50


def test_openapi_docs_are_gated():
    assert all(needs_login(p, "GET") for p in ("/docs", "/redoc", "/openapi.json"))
