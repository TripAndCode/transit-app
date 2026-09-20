"""GET /api/admin/users/{uid} reports BYOK presence (provider name only,
never the key or its suffix) -- exercised with a fake connection, see
tests/unit/test_admin_bounds.py for the pattern.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.deps import get_conn
from api.routers import admin as admin_router
from api.security import User, require_admin

_ADMIN = User(
    user_id=1,
    email="admin@example.com",
    name="Admin",
    avatar_url=None,
    role="admin",
    suspended_at=None,
    llm_approved=True,
)


class _FakeConn:
    def __init__(self, *, byok_provider: str | None):
        self._byok_provider = byok_provider

    async def fetchrow(self, sql, *args):
        if "FROM users WHERE user_id=$1" in sql:
            return {
                "user_id": 7,
                "email": "u@x",
                "name": None,
                "avatar_url": None,
                "role": "user",
                "suspended_at": None,
                "llm_approved": False,
                "created_at": datetime.now(timezone.utc),
            }
        if "FROM user_llm_keys WHERE user_id=$1" in sql:
            return {"provider": self._byok_provider} if self._byok_provider else None
        raise AssertionError(f"unexpected fetchrow: {sql}")

    async def fetch(self, sql, *args):
        return []


def _client(conn: _FakeConn) -> TestClient:
    app = FastAPI()
    app.include_router(admin_router.router)
    app.dependency_overrides[require_admin] = lambda: _ADMIN
    app.dependency_overrides[get_conn] = lambda: conn
    return TestClient(app)


def test_byok_provider_none_when_not_configured():
    r = _client(_FakeConn(byok_provider=None)).get("/api/admin/users/7")
    assert r.status_code == 200
    assert r.json()["byok_provider"] is None


def test_byok_provider_reports_provider_without_the_key():
    r = _client(_FakeConn(byok_provider="openai")).get("/api/admin/users/7")
    assert r.status_code == 200
    body = r.json()
    assert body["byok_provider"] == "openai"
    assert "key" not in body
    assert "suffix" not in body
