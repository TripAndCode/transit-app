"""Client-disconnect → asyncpg query cancellation, end to end.

The SPA aborts in-flight GETs whenever a filter changes (react-query's
AbortSignal, threaded through apiGet since PR #43). That only saves work
if the *server* also stops: uvicorn must cancel the request task on the
TCP disconnect, and asyncpg must translate that CancelledError into a
server-side query cancellation.

uvicorn does NOT do this by itself (verified: without the middleware this
test fails — the query runs to completion). CancelGETOnDisconnectMiddleware
watches the receive channel for ``http.disconnect`` and cancels the
handler task; this test pins that chain end-to-end against a real uvicorn
server and a real socket — ASGITransport can't model TCP disconnects.

The app here is minimal but reproduces the production execution shape:
lifespan-owned asyncpg pool, plain awaited query in the handler (see
api/routers/map.py), and the same middleware wiring as api/main.py.
"""

import asyncio
import socket

import asyncpg
import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from api.middleware.cancel_on_disconnect import CancelGETOnDisconnectMiddleware
from tests.conftest import DATABASE_URL

SLEEP_SECONDS = 30  # far longer than the test runs; only ever cancelled
MARKER = "/* cancellation-test */"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _build_app() -> FastAPI:
    """Minimal app reproducing the production execution shape:
    lifespan-owned asyncpg pool, handler does a plain awaited query."""
    app = FastAPI()

    @app.on_event("startup")
    async def _startup() -> None:
        app.state.pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=2)

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        await app.state.pool.close()

    @app.get("/slow")
    async def slow(request: Request) -> JSONResponse:
        pool = request.app.state.pool
        async with pool.acquire() as conn:
            await conn.execute(f"SELECT {MARKER} pg_sleep({SLEEP_SECONDS})")
        return JSONResponse({"ok": True})

    app.add_middleware(CancelGETOnDisconnectMiddleware)
    return app


async def _count_running(conn: asyncpg.Connection) -> int:
    return await conn.fetchval(
        """
        SELECT count(*) FROM pg_stat_activity
        WHERE state = 'active'
          AND query LIKE $1
          AND pid <> pg_backend_pid()
        """,
        f"%{MARKER}%",
    )


@pytest.mark.asyncio
async def test_client_disconnect_cancels_running_query(apply_schema):
    port = _free_port()
    config = uvicorn.Config(_build_app(), host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    server_task = asyncio.create_task(server.serve())
    try:
        # Wait for the server to accept connections.
        for _ in range(100):
            if server.started:
                break
            await asyncio.sleep(0.05)
        assert server.started, "uvicorn did not start"

        watcher = await asyncpg.connect(DATABASE_URL)
        try:
            # Raw socket client: send the request, then hard-close mid-query.
            _reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b"GET /slow HTTP/1.1\r\nHost: x\r\n\r\n")
            await writer.drain()

            # Positive control — the query must actually be running before
            # we disconnect, otherwise the assertion below proves nothing.
            for _ in range(100):
                if await _count_running(watcher) > 0:
                    break
                await asyncio.sleep(0.05)
            assert await _count_running(watcher) > 0, "pg_sleep never started"

            # Hard TCP disconnect (what an aborted fetch() does).
            writer.close()
            await writer.wait_closed()

            # uvicorn cancels the request task → asyncpg cancels the query.
            deadline = asyncio.get_event_loop().time() + 5.0
            remaining = 1
            while asyncio.get_event_loop().time() < deadline:
                remaining = await _count_running(watcher)
                if remaining == 0:
                    break
                await asyncio.sleep(0.1)
            assert remaining == 0, (
                "server-side query still running after client disconnect — "
                "task cancellation is not propagating to asyncpg"
            )
        finally:
            await watcher.close()
    finally:
        server.should_exit = True
        await server_task
