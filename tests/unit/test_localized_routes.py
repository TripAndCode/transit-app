"""Which routes read the request's language, and how the frontend must fetch each.

A GET response holding text written in the request's Accept-Language is only
good for that language, so the frontend fetches each route in
``LOCALIZED_GET_ROUTES`` through ``useLocalizedQuery`` (frontend/src/api/hooks.ts),
which keys the cache on the UI language. A route that starts taking
``Depends(get_locale)`` fails here until it joins one of the sets below, and
that is the point to decide how its client caches it.

Only ``Depends(get_locale)`` is seen. A direct ``get_locale(request)`` call is
not: the aggregate-not-ready 503 handler makes one, and the SPA shows its own
text for that error's ``code`` rather than the server's ``detail``.
"""

from fastapi.routing import APIRoute, iter_route_contexts

from api.deps import get_locale
from api.main import app

LOCALIZED_GET_ROUTES = {
    ("GET", "/api/{agency_id}/forecast/heatmap"),
    ("GET", "/api/{agency_id}/forecast/overview"),
    ("GET", "/api/{agency_id}/performance_standards"),
    ("GET", "/api/{agency_id}/reports/suggest"),
    ("GET", "/api/{agency_id}/reports/{report_type}"),
    ("GET", "/api/{agency_id}/route-shape"),
    ("GET", "/api/{agency_id}/weather_delay"),
}

# Take the locale but write no text in it: the overview payload's strings are
# all rendered client-side, and the build schema returns both languages'
# labels at once. Their frontend keys carry no language.
LOCALE_UNUSED_GET_ROUTES = {
    ("GET", "/api/{agency_id}/ask/build-schema"),
    ("GET", "/api/{agency_id}/overview/summary"),
}

# Not cached as GET responses: Ask answers and follow-ups are mutations, and
# the Copilot insight's own query key already carries the language.
LOCALIZED_POST_ROUTES = {
    ("POST", "/api/{agency_id}/ask"),
    ("POST", "/api/{agency_id}/conversations/{conversation_id}/followup"),
    ("POST", "/api/{agency_id}/conversations/{conversation_id}/messages"),
    ("POST", "/api/{agency_id}/copilot/insight"),
}


def _depends_on(dependant, call) -> bool:
    return any(sub.call is call or _depends_on(sub, call) for sub in dependant.dependencies)


def _routes_reading_the_locale() -> set[tuple[str, str]]:
    # iter_route_contexts, not app.routes: this FastAPI keeps each included
    # router in app.routes as one opaque entry.
    found = set()
    for context in iter_route_contexts(app.routes):
        route = context.original_route
        if isinstance(route, APIRoute) and context.path and _depends_on(route.dependant, get_locale):
            found |= {(method, context.path) for method in context.methods or ()}
    return found


def test_every_route_reading_the_locale_is_classified():
    assert _routes_reading_the_locale() == LOCALIZED_GET_ROUTES | LOCALE_UNUSED_GET_ROUTES | LOCALIZED_POST_ROUTES


def test_the_walk_sees_a_localized_route_inside_an_included_router():
    assert ("GET", "/api/{agency_id}/route-shape") in _routes_reading_the_locale()
