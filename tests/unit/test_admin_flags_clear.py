"""``DELETE /api/admin/flags/{key}`` removes a flag's override.

Exercised through a minimal standalone app (not ``api.main``) with
``require_admin``/``get_conn`` overridden, so nothing here touches a real
pool or database -- the same pattern as ``tests/unit/test_admin_bounds.py``.
The flag cache is fed a stub loader for the same reason: the handler resolves
the flag's state before and after the delete, and that read must not reach
for Postgres from a unit test.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.deps import get_conn
from api.routers import admin_flags
from api.security import User, require_admin
from pipeline import flags

_ADMIN = User(
    user_id=42,
    email="admin@example.com",
    name="Admin",
    avatar_url=None,
    role="admin",
    suspended_at=None,
    llm_approved=True,
)

_KEY = "weather_ingest_enabled"

#: What `feature_flags` holds for `_KEY` before the request under test.
_OVERRIDE_ROW = (True, "pilot rollout", 7, None)


class _Transaction:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_exc: Any) -> bool:
        return False


class _Conn:
    """Stands in for the `feature_flags` table plus the audit sink.

    The DELETE mutates `rows`, which is also what the flag cache's stubbed
    loader reads, so the handler's post-delete re-resolve sees the same
    store the delete acted on.
    """

    def __init__(self, rows: dict[str, tuple[Any, ...]]) -> None:
        self.rows = rows
        self.deletes: list[tuple[Any, ...]] = []
        self.audit: list[tuple[Any, ...]] = []

    def transaction(self) -> _Transaction:
        return _Transaction()

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any] | None:
        assert "DELETE FROM feature_flags" in sql, f"unexpected fetchrow: {sql}"
        self.deletes.append(args)
        removed = self.rows.pop(args[0], None)
        return None if removed is None else {"value": removed[0]}

    async def execute(self, sql: str, *args: Any) -> str:
        assert "INSERT INTO admin_audit" in sql, f"unexpected execute: {sql}"
        self.audit.append(args)
        return "INSERT 1"


def _client(conn: _Conn) -> TestClient:
    app = FastAPI()
    app.include_router(admin_flags.router)
    app.dependency_overrides[require_admin] = lambda: _ADMIN
    app.dependency_overrides[get_conn] = lambda: conn
    return TestClient(app)


@pytest.fixture(autouse=True)
def _no_csrf(monkeypatch):
    monkeypatch.setattr(admin_flags, "csrf_guard", lambda _request: None)


@pytest.fixture
def store(monkeypatch) -> dict[str, tuple[Any, ...]]:
    """The stubbed `feature_flags` contents, shared by the fake connection
    and the flag cache's loader."""
    rows: dict[str, tuple[Any, ...]] = {_KEY: _OVERRIDE_ROW}
    monkeypatch.setattr(flags, "_load_overrides", lambda: dict(rows))
    monkeypatch.setenv("WEATHER_INGEST_ENABLED", "false")
    flags.reset_cache()
    yield rows
    flags.reset_cache()


def test_clearing_an_override_deletes_the_row_and_returns_the_env_value(store):
    conn = _Conn(store)
    r = _client(conn).delete(f"/api/admin/flags/{_KEY}")

    assert r.status_code == 200
    body = r.json()
    assert body["key"] == _KEY
    assert body["source"] == "env"
    assert body["value"] is False
    assert body["updated_by"] is None
    assert body["reason"] is None
    assert conn.deletes == [(_KEY,)]
    assert _KEY not in store


def test_clearing_an_override_records_the_admin_action(store):
    conn = _Conn(store)
    r = _client(conn).delete(f"/api/admin/flags/{_KEY}")

    assert r.status_code == 200
    assert len(conn.audit) == 1
    actor_id, action, target_type, target_id, before, after, _reason = conn.audit[0]
    assert actor_id == _ADMIN.user_id
    assert action == "flag.cleared"
    assert target_type == "feature_flag"
    assert target_id == _KEY
    assert '"value": true' in before
    assert '"value": false' in after


def test_clearing_an_unknown_key_is_a_404_and_touches_nothing(store):
    conn = _Conn(store)
    r = _client(conn).delete("/api/admin/flags/not_a_real_flag")

    assert r.status_code == 404
    assert conn.deletes == []
    assert conn.audit == []
    assert _KEY in store


def test_clearing_a_flag_with_no_override_is_a_no_op_not_an_audit_entry(store):
    """A DELETE the table already satisfies stays idempotent: the flag is
    already on its env value, and an audit entry for a change that did not
    happen would be a false trail."""
    store.pop(_KEY)
    flags.reset_cache()
    conn = _Conn(store)

    r = _client(conn).delete(f"/api/admin/flags/{_KEY}")

    assert r.status_code == 200
    assert r.json()["source"] == "env"
    assert conn.deletes == [(_KEY,)]
    assert conn.audit == []
