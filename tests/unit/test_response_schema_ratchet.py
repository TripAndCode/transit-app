"""Every public endpoint should describe its response body in the OpenAPI schema.

Without a response model an endpoint contributes nothing to the generated
schema, so the only description of its shape is the hand-written mirror in
`frontend/src/api/types.ts` -- which nothing checks against the server. This
test pins the set of endpoints still in that state so it can only shrink.
"""

from collections.abc import Sequence

from fastapi.routing import APIRoute
from starlette.routing import BaseRoute

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
#
# Annotating a handler `-> dict[str, Any]` does not count as describing it, and
# does not belong here as a way off this list: FastAPI infers a response model
# from the return annotation, so `Any` produces a schema that says "object" and
# constrains nothing -- while still routing the payload through Pydantic
# serialization, where a Decimal renders as a JSON string instead of a number.
# Those handlers carry `response_model=None` to keep FastAPI's plain
# jsonable_encoder path, and stay listed here until they gain a real model.
PENDING = {
    "GET /api/{agency_id}/ask/build-schema",
    "GET /api/{agency_id}/ask/dashboard/anomalies",
    "GET /api/{agency_id}/ask/dashboard/heatmap",
    "GET /api/{agency_id}/ask/dashboard/movers",
    "GET /api/{agency_id}/ask/followup-enabled",
    "GET /api/{agency_id}/conversations",
    "GET /api/{agency_id}/conversations/{conversation_id}",
    "GET /api/{agency_id}/conversations/{conversation_id}/messages",
    "GET /api/{agency_id}/delays/heatmap",
    "GET /api/{agency_id}/delays/live",
    "GET /api/{agency_id}/delays/live-progress",
    "GET /api/{agency_id}/route-shape",
    "GET /api/{agency_id}/today/route-summary",
    "GET /api/{agency_id}/today/route/{route_code}/stop-profile",
    "PATCH /api/{agency_id}/conversations/{conversation_id}",
    "POST /api/{agency_id}/conversations",
    "POST /api/{agency_id}/conversations/{conversation_id}/followup",
    "POST /api/{agency_id}/conversations/{conversation_id}/messages",
    "POST /api/{agency_id}/delays/refresh",
    # Returns a raw Response subclass (JSONResponse), not a schema-describable
    # model -- FastAPI does not infer a response_model from that return
    # annotation, so this one stays pending until it grows a real model.
    "POST /api/auth/local/login",
}

# Endpoints whose typed response is load-bearing and must stay typed. Shrinking
# PENDING is the ratchet's one direction; this is its counterweight — an
# endpoint listed here fails the suite the moment it loses its response model,
# instead of quietly rejoining the undescribed set PENDING is allowed to hold.
# Grow this whenever an endpoint is promoted out of PENDING.
TYPED = {
    "DELETE /api/admin/flags/{key}",
    "GET /api/{agency_id}/delays/timeline",
    "GET /api/{agency_id}/today/route/{route_code}/trips",
}


def _schema_routes() -> list[tuple[str, APIRoute]]:
    out = []

    def _collect(routes: Sequence[BaseRoute]) -> None:
        for route in routes:
            if isinstance(route, APIRoute):
                if not route.include_in_schema or route.methods is None:
                    continue
                for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
                    out.append((f"{method} {route.path}", route))
            elif hasattr(route, "original_router"):
                # Newer FastAPI wraps each include_router() call in a private
                # lazy-matching wrapper instead of flattening its routes
                # directly into app.routes; recurse into the underlying
                # router (whose routes already carry its own prefix) so this
                # still sees the real endpoint set on either FastAPI version.
                _collect(route.original_router.routes)

    _collect(app.routes)
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


def test_typed_endpoints_still_declare_a_response_model():
    regressed = TYPED & _undescribed()
    assert not regressed, f"These lost their response model: {sorted(regressed)}"


def test_typed_endpoints_all_exist():
    known = {name for name, _ in _schema_routes()}
    assert not (TYPED - known), f"Unknown routes listed as typed: {sorted(TYPED - known)}"
