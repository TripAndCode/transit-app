"""Every public endpoint should describe its response body in the OpenAPI schema.

Without a response model an endpoint contributes nothing to the generated
schema, so the only description of its shape is the hand-written mirror in
`frontend/src/api/types.ts` -- which nothing checks against the server. This
test pins the set of endpoints still in that state so it can only shrink.
"""

from fastapi.routing import APIRoute

from api.main import app

# A 204 has no body by definition, so there is nothing to describe. Structural,
# not a backlog entry: these never acquire a response model.
_NO_CONTENT = 204

# Endpoints that answer with a redirect rather than a body. The browser follows
# it; no client ever parses a payload here, so a response model would describe
# something that does not exist.
PERMANENTLY_EXEMPT = {
    "GET /api/auth/{provider}/login",
    "GET /api/auth/{provider}/callback",
}

# Endpoints that should describe their response and do not yet. Shrink this;
# never grow it. A new endpoint declares its response model instead of being
# added here.
PENDING = {
    "GET /api/{agency_id}/ask/build-schema",
    "GET /api/{agency_id}/ask/suggest",
    "POST /api/{agency_id}/ask/edit-action",
    "GET /api/{agency_id}/ask/dashboard/heatmap",
    "GET /api/{agency_id}/ask/dashboard/anomalies",
    "GET /api/{agency_id}/ask/dashboard/movers",
    "POST /api/auth/local/login",
    "GET /api/{agency_id}/conversations",
    "POST /api/{agency_id}/conversations",
    "GET /api/{agency_id}/conversations/{conversation_id}",
    "PATCH /api/{agency_id}/conversations/{conversation_id}",
    "DELETE /api/{agency_id}/conversations/{conversation_id}",
    "GET /api/{agency_id}/conversations/{conversation_id}/messages",
    "POST /api/{agency_id}/conversations/migrate-anon",
    "POST /api/{agency_id}/conversations/{conversation_id}/messages",
    "POST /api/{agency_id}/conversations/{conversation_id}/followup",
    "GET /api/{agency_id}/ask/followup-enabled",
    "GET /api/{agency_id}/copilot/enabled",
    "GET /api/{agency_id}/delays/live",
    "GET /api/{agency_id}/delays/live-progress",
    "POST /api/{agency_id}/delays/refresh",
    "GET /api/{agency_id}/route-shape",
    "GET /api/{agency_id}/today/route-summary",
    "GET /api/{agency_id}/today/route/{route_code}/trips",
    "GET /api/{agency_id}/today/route/{route_code}/stop-profile",
    "GET /api/{agency_id}/delays/heatmap",
}


def _schema_routes() -> list[tuple[str, APIRoute]]:
    out = []
    for route in app.routes:
        if not isinstance(route, APIRoute) or not route.include_in_schema:
            continue
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            out.append((f"{method} {route.path}", route))
    return out


def _undescribed() -> set[str]:
    return {
        name for name, route in _schema_routes() if route.response_model is None and route.status_code != _NO_CONTENT
    }


def test_no_endpoint_lacks_a_response_model_outside_the_known_set():
    unexpected = _undescribed() - PENDING - PERMANENTLY_EXEMPT
    assert not unexpected, (
        "These endpoints declare no response model, so they contribute nothing to "
        f"the OpenAPI schema: {sorted(unexpected)}. Declare one, or — only for a "
        "redirect or an empty body — add it to PERMANENTLY_EXEMPT with a reason."
    )


def test_pending_set_contains_no_endpoint_that_now_describes_itself():
    """Keeps the ratchet honest: a fixed endpoint must leave PENDING."""
    stale = PENDING - _undescribed()
    assert not stale, f"These now declare a response model and must be removed from PENDING: {sorted(stale)}"


def test_permanently_exempt_endpoints_all_exist():
    """A renamed or deleted route must not leave a silent hole in the exemptions."""
    known = {name for name, _ in _schema_routes()}
    assert not (PERMANENTLY_EXEMPT - known), f"Unknown routes exempted: {sorted(PERMANENTLY_EXEMPT - known)}"
