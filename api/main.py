import os
from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://localhost/transit")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pool = await asyncpg.create_pool(DATABASE_URL)
    yield
    await app.state.pool.close()


app = FastAPI(title="Transit Delay API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

from api.routers.agencies import router as agencies_router
from api.routers.ask import router as ask_router
from api.routers.query import router as query_router
from api.routers.ws import router as ws_router
app.include_router(agencies_router)
app.include_router(ask_router)
app.include_router(query_router)
app.include_router(ws_router)


@app.get("/health")
async def health():
    return {"status": "ok"}
