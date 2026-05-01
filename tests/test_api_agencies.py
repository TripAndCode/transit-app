import pytest
import httpx
from httpx import ASGITransport
import asyncpg
import os

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://localhost/transit")


@pytest.fixture
async def app_client(apply_schema):
    from api.main import app
    pool = await asyncpg.create_pool(DATABASE_URL)
    app.state.pool = pool
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client
    await pool.close()


@pytest.mark.asyncio
async def test_health_endpoint(app_client):
    resp = await app_client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
