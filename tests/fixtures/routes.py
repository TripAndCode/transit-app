"""Every endpoint an app serves, for tests that check a property of each."""

from collections.abc import Iterator, Sequence

from fastapi.routing import APIRoute
from starlette.routing import BaseRoute


def api_routes(routes: Sequence[BaseRoute]) -> Iterator[APIRoute]:
    """Each endpoint in *routes*. Newer FastAPI wraps each include_router()
    call in a private lazy-matching wrapper instead of flattening its routes
    into app.routes, so this recurses into the underlying router, whose routes
    already carry its prefix, and sees the real endpoint set on either
    FastAPI version."""
    for route in routes:
        if isinstance(route, APIRoute):
            yield route
        elif hasattr(route, "original_router"):
            yield from api_routes(route.original_router.routes)
