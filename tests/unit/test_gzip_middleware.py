"""Responses are gzip-compressed for clients that accept it.

Map and report endpoints return large JSON; without compression a network-wide
map is megabytes per request. The layer is innermost, next to the routers, so
it sees each response whole; outer middlewares stream the body, and a streamed
body is compressed whatever its size.
"""

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient
from starlette.middleware.gzip import GZipMiddleware

from api.main import app
from api.middleware.request_log import RequestLogMiddleware


def _stack() -> list[type]:
    # user_middleware is outermost first.
    return [m.cls for m in app.user_middleware]


def test_responses_are_compressed_innermost():
    stack = _stack()
    assert stack[-1] is GZipMiddleware
    assert stack.index(RequestLogMiddleware) < stack.index(GZipMiddleware)


def test_small_responses_are_left_uncompressed():
    (gzip,) = [m for m in app.user_middleware if m.cls is GZipMiddleware]
    assert gzip.kwargs["minimum_size"] >= 1000


@pytest.fixture
def probe_routes():
    router = APIRouter()

    @router.get("/api/__gzip_probe_small")
    def small():
        return {"x": "a"}

    @router.get("/api/__gzip_probe_large")
    def large():
        return {"x": "a" * 5000}

    added = list(router.routes)
    app.router.routes[0:0] = added
    try:
        yield TestClient(app)
    finally:
        for route in added:
            app.router.routes.remove(route)


@pytest.mark.parametrize(
    ("path", "accept", "encoding"),
    [
        ("/api/__gzip_probe_large", "gzip", "gzip"),
        ("/api/__gzip_probe_small", "gzip", None),
        ("/api/__gzip_probe_large", "identity", None),
    ],
)
def test_a_response_is_compressed_only_when_large_and_accepted(probe_routes, path, accept, encoding):
    response = probe_routes.get(path, headers={"accept-encoding": accept})
    assert response.status_code == 200
    assert response.headers.get("content-encoding") == encoding
