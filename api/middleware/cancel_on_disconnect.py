"""Cancel GET request handlers when the client disconnects.

uvicorn does NOT cancel an in-flight handler task when the TCP connection
drops — it only delivers an ``http.disconnect`` message that nobody reads
unless the app awaits ``receive()`` (which a GET handler never does). The
result: when the SPA aborts a fetch (filter change mid-flight), the
handler keeps running and its asyncpg query scans to completion. Rapid
filter changes stack abandoned multi-second heatmap scans on the pool.

This middleware gives the downstream app a synthetic empty-body receive
(safe for GET — there is no request body to consume) and uses the real
``receive`` channel to watch for ``http.disconnect``. On disconnect it
cancels the handler task; the CancelledError propagates into asyncpg,
which cancels the server-side query.

Scope is deliberately GET-only: mutating requests should run to
completion even if the client gives up, and non-GET bodies would need
real receive plumbing.

Verified end-to-end by tests/test_request_cancellation.py.
"""

import asyncio
from contextlib import suppress

from starlette.types import ASGIApp, Message, Receive, Scope, Send


class CancelGETOnDisconnectMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") != "GET":
            await self.app(scope, receive, send)
            return

        sent_request = False

        async def app_receive() -> Message:
            # First call: the (empty) request body. Afterwards: pend forever —
            # the real channel is owned by the disconnect watcher below.
            nonlocal sent_request
            if not sent_request:
                sent_request = True
                return {"type": "http.request", "body": b"", "more_body": False}
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        async def watch_disconnect() -> None:
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return

        app_task = asyncio.ensure_future(self.app(scope, app_receive, send))
        watch_task = asyncio.ensure_future(watch_disconnect())
        try:
            await asyncio.wait({app_task, watch_task}, return_when=asyncio.FIRST_COMPLETED)
            if not app_task.done():
                # Client went away first — stop the handler (and its query).
                app_task.cancel()
                with suppress(asyncio.CancelledError):
                    await app_task
            else:
                # Normal completion — re-raise handler exceptions, if any.
                await app_task
        finally:
            if not watch_task.done():
                watch_task.cancel()
                with suppress(asyncio.CancelledError):
                    await watch_task
