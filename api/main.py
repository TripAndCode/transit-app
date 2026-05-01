import os
from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from api.middleware.auth import APIKeyMiddleware
from api.middleware.ratelimit import limiter
from api.routers.agencies import router as agencies_router
from api.routers.ask import router as ask_router
from api.routers.map import router as map_router
from api.routers.query import router as query_router
from api.routers.static import router as static_router
from api.routers.ws import router as ws_router

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://localhost/transit")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pool = await asyncpg.create_pool(DATABASE_URL)
    yield
    await app.state.pool.close()


app = FastAPI(title="Transit Delay API", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(APIKeyMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(agencies_router)
app.include_router(ask_router)
app.include_router(query_router)
app.include_router(ws_router)
app.include_router(map_router)
app.include_router(static_router)


@app.get("/health")
async def health():
    return {"status": "ok"}
