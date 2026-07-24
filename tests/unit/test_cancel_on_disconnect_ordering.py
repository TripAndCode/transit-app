"""CancelGETOnDisconnectMiddleware sits inside LocaleMiddleware (a
BaseHTTPMiddleware) in production - see api/main.py's add_middleware order,
which nests it under Locale/APIKey/Session. BaseHTTPMiddleware's call_next
requires the wrapped app to eventually send a response; the cancel-on-
disconnect path must not return without ever calling send(), or every
legitimate client disconnect crashes with RuntimeError: No response
returned, even though the query cancellation itself worked.

DB-free: LocaleMiddleware needs no DB, and the endpoint here never
actually queries anything - it just hangs until cancelled, standing in
for the real asyncpg query cancellation covered end-to-end by
tests/api/test_request_cancellation.py.
"""

import asyncio

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from api.middleware.cancel_on_disconnect import CancelGETOnDisconnectMiddleware
from api.middleware.locale import LocaleMiddleware


async def _slow_endpoint(request):
    await asyncio.Event().wait()
    return JSONResponse({"ok": True})  # unreachable; only cancellation ends this


def _build_app() -> Starlette:
    app = Starlette(routes=[Route("/slow", _slow_endpoint)])
    app.add_middleware(CancelGETOnDisconnectMiddleware)
    app.add_middleware(LocaleMiddleware)
    return app


@pytest.mark.asyncio
async def test_disconnect_cancel_does_not_raise_when_wrapped_by_basehttpmiddleware():
    app = _build_app()
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/slow",
        "headers": [],
        "query_string": b"",
        "http_version": "1.1",
        "scheme": "http",
        "client": ("test", 1234),
        "server": ("test", 80),
    }

    sent_request = False

    async def receive():
        nonlocal sent_request
        if not sent_request:
            sent_request = True
            return {"type": "http.request", "body": b"", "more_body": False}
        return {"type": "http.disconnect"}

    messages = []

    async def send(message):
        messages.append(message)

    await app(scope, receive, send)  # must not raise RuntimeError: No response returned.
    assert any(m["type"] == "http.response.start" for m in messages)


@pytest.mark.asyncio
async def test_disconnect_cancel_does_not_double_start_a_response_already_in_flight():
    """If the wrapped app already sent http.response.start before being
    cancelled (e.g. a future streaming GET mid-body), the middleware must
    not send a second one - two http.response.start messages is an ASGI
    protocol violation, distinct from the no-response-at-all case the
    other test covers."""

    async def _streaming_app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"partial", "more_body": True})
        await asyncio.Event().wait()  # hangs mid-body until cancelled

    middleware = CancelGETOnDisconnectMiddleware(_streaming_app)

    scope = {"type": "http", "method": "GET", "path": "/stream"}
    sent_request = False

    async def receive():
        nonlocal sent_request
        if not sent_request:
            sent_request = True
            return {"type": "http.request", "body": b"", "more_body": False}
        return {"type": "http.disconnect"}

    messages = []

    async def send(message):
        messages.append(message)

    await middleware(scope, receive, send)
    starts = [m for m in messages if m["type"] == "http.response.start"]
    assert len(starts) == 1
