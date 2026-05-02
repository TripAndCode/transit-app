import os
import os.path
from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not os.environ.get("GROQ_API_KEY"):
        raise RuntimeError("GROQ_API_KEY env var is required")
    app.state.pool = await asyncpg.create_pool(DATABASE_URL)
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
    return {"status": "ok"}


def _maybe_mount_static(app: FastAPI) -> None:
    """Mount built SPA at root if api/static/ exists. Idempotent (re-callable in tests)."""
    if not os.path.isdir(STATIC_DIR):
        return

    # Remove any prior SPA fallback + assets mount so re-mount in tests works correctly
    app.routes[:] = [
        r for r in app.routes
        if getattr(r, "name", None) not in ("spa_fallback", "assets")
    ]

    assets_dir = os.path.join(STATIC_DIR, "assets")
    if os.path.isdir(assets_dir):
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    index_path = os.path.join(STATIC_DIR, "index.html")

    @app.get("/{full_path:path}", include_in_schema=False, name="spa_fallback")
    async def spa_fallback(full_path: str):
        return FileResponse(index_path)


_maybe_mount_static(app)
