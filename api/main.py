"""FastAPI application bootstrap.

Wires routers, middleware, lifespan (asyncpg pool + Groq key validation), CORS,
and an optional SPA static mount. In production the multistage Dockerfile copies
the built React frontend into ``api/static/``; this module then mounts the SPA
at ``/`` with an explicit JSON 404 for unknown ``/api/*`` paths so frontend
fetches keep getting structured errors instead of HTML index pages.
"""

import os
import os.path
from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from api.middleware.auth import APIKeyMiddleware
from api.middleware.ratelimit import limiter
from api.routers.agencies import router as agencies_router
from api.routers.ask import router as ask_router
from api.routers.map import router as map_router
from api.routers.query import router as query_router
from api.routers.reports import router as reports_router
from api.routers.static import router as static_router

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://localhost/transit")
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

# Path prefixes the SPA fallback must NOT swallow. An unknown URL under one of
# these prefixes should still return a structured JSON 404, not the index.html,
# so frontend fetches surface real errors instead of choking on HTML bodies.
# NOTE: ``agencies`` is intentionally NOT in this list — the SPA uses paths
# like ``/agencies/1/map`` for client-side routing. The backend's actual
# ``/agencies`` and ``/agencies/{id}`` routes are matched by the router
# before the fallback ever runs, so legitimate API requests are unaffected.
_API_PREFIXES = ("api/", "health", "docs", "redoc", "openapi.json")


async def _init_connection(conn: asyncpg.Connection) -> None:
    """Per-connection setup. Pin the session to Asia/Tokyo so ``captured_at::date``
    casts honor the operator's local calendar instead of UTC (Aomori observations
    span midnight JST and would otherwise straddle two UTC dates)."""
    await conn.execute("SET TIME ZONE 'Asia/Tokyo'")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Validate required env, open the asyncpg pool, and tear it down on exit."""
    if not os.environ.get("GROQ_API_KEY"):
        raise RuntimeError("GROQ_API_KEY env var is required")
    app.state.pool = await asyncpg.create_pool(DATABASE_URL, init=_init_connection)
    yield
    await app.state.pool.close()


_CORS_ORIGINS = [o.strip() for o in os.environ.get("CORS_ORIGINS", "http://localhost:5173").split(",") if o.strip()]

app = FastAPI(title="Transit Delay API", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(APIKeyMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-API-Key"],
)

app.include_router(agencies_router)
app.include_router(ask_router)
app.include_router(query_router)
app.include_router(reports_router)
app.include_router(map_router)
app.include_router(static_router)


@app.get("/health")
async def health():
    """Liveness probe. Returns ``{"status": "ok"}`` once the app is responding."""
    return {"status": "ok"}


def _maybe_mount_static(app: FastAPI) -> None:
    """Mount the built SPA at ``/`` if ``api/static/`` exists.

    Idempotent: prior ``assets`` and ``spa_fallback`` routes are stripped before
    re-adding, so this can be called repeatedly (e.g. from pytest fixtures
    monkeypatching ``STATIC_DIR``).

    The catch-all ``spa_fallback`` route serves ``index.html`` for any path that
    no router consumed, EXCEPT paths starting with API/health/doc prefixes —
    those return a JSON 404 so the frontend fetch layer can render a real error
    instead of trying to JSON-parse the SPA HTML.
    """
    if not os.path.isdir(STATIC_DIR):
        return

    app.routes[:] = [r for r in app.routes if getattr(r, "name", None) not in ("spa_fallback", "assets")]

    assets_dir = os.path.join(STATIC_DIR, "assets")
    if os.path.isdir(assets_dir):
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    index_path = os.path.join(STATIC_DIR, "index.html")

    @app.get("/{full_path:path}", include_in_schema=False, name="spa_fallback")
    async def spa_fallback(full_path: str):
        """Serve ``index.html`` for SPA routes; JSON 404 for unknown API paths."""
        if full_path.startswith(_API_PREFIXES):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        return FileResponse(index_path)


_maybe_mount_static(app)
