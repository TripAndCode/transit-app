from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from api.middleware.auth import APIKeyMiddleware


def _make_app(fetchrow_result):
    app = FastAPI()
    app.add_middleware(APIKeyMiddleware)

    mock_pool = MagicMock()
    mock_pool.fetchrow = AsyncMock(return_value=fetchrow_result)
    app.state.pool = mock_pool

    @app.get("/test")
    async def test_route(request: Request):
        return {"ok": True}

    return app


def test_no_api_key_allows_request():
    app = _make_app(fetchrow_result=None)
    client = TestClient(app)
    response = client.get("/test")
    assert response.status_code == 200


def test_valid_api_key_allows_request():
    mock_row = {"tier": "pro"}
    app = _make_app(fetchrow_result=mock_row)
    client = TestClient(app)
    response = client.get("/test", headers={"X-API-Key": "valid-key"})
    assert response.status_code == 200


def test_invalid_api_key_returns_401():
    app = _make_app(fetchrow_result=None)
    client = TestClient(app)
    response = client.get("/test", headers={"X-API-Key": "bad-key"})
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid API key"
