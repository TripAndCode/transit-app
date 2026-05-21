"""CORS response shape for cross-origin requests from the Vite dev server.

For SSO to work on http://localhost:5173, the FastAPI app at :8000 must
echo both ``Access-Control-Allow-Origin: http://localhost:5173`` and
``Access-Control-Allow-Credentials: true`` so the browser will accept
the response when the fetch is sent with ``credentials: 'include'``.
"""

import os

import asyncpg
import httpx
import pytest
from httpx import ASGITransport

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://localhost/transit")


@pytest.fixture
async def client(apply_schema):
    """Boot the real app against the test DB pool, talk to it via ASGI."""
    from api.main import app

    pool = await asyncpg.create_pool(DATABASE_URL)
    app.state.pool = pool
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    await pool.close()


@pytest.mark.asyncio
async def test_cors_allows_credentials_from_localhost_5173(client):
    """Cross-origin GET with Origin=http://localhost:5173 must come back
    with both ACAC=true and ACAO echoing the request origin."""
    resp = await client.get(
        "/api/agencies",
        headers={"Origin": "http://localhost:5173"},
    )
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-credentials") == "true"
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:5173"


@pytest.mark.asyncio
async def test_cors_rejects_unlisted_origin(client):
    """An origin not in CORS_ORIGINS gets no ACAO header (browser then
    blocks the credentialed response). The body is still served, but
    the missing header is the gate."""
    resp = await client.get(
        "/api/agencies",
        headers={"Origin": "https://evil.example.com"},
    )
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") in (None, "")
