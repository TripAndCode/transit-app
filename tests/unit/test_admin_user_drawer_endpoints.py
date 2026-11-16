"""Router-shape tests for the admin user drawer's sessions endpoints: request
validation and the exact-prefix session lookup.

Exercised through a minimal standalone app (not ``api.main``) with
``require_admin``/``get_conn`` overridden to a fake in-memory connection, so
this never touches a real database -- see ``tests/unit/test_admin_bounds.py``
for the same pattern.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.deps import get_conn
from api.routers import admin as admin_router
from api.security import require_admin
from tests.fixtures.users import admin_user

_ADMIN = admin_user()

_ORIGIN = {"Origin": "http://test"}


class _FakeConn:
    """In-memory stand-in for the tiny set of queries these endpoints issue.
    Dispatches on a recognizable substring of the SQL text, matching the
    exact queries written in ``api/routers/admin.py``."""

    def __init__(self, *, sessions=None):
        self.sessions = sessions or []  # list of dict(sid_hash, user_id)
        self.events: list[tuple] = []
        self.audit: list[tuple] = []

    def transaction(self):
        """The handlers wrap their write and its audit entry together; this
        fake has no rollback to model, so the block just runs."""

        @asynccontextmanager
        async def _noop():
            yield

        return _noop()

    async def fetch(self, sql, *args):
        if "FROM sessions WHERE user_id=$1" in sql:
            (uid,) = args
            return [
                {"sid_hash": s["sid_hash"]} | {k: v for k, v in s.items() if k != "sid_hash"}
                for s in self.sessions
                if s["user_id"] == uid
            ]
        raise AssertionError(f"unexpected fetch: {sql}")

    async def fetchval(self, sql, *args):
        raise AssertionError(f"unexpected fetchval: {sql}")

    async def fetchrow(self, sql, *args):
        raise AssertionError(f"unexpected fetchrow: {sql}")

    async def execute(self, sql, *args):
        if "DELETE FROM sessions" in sql:
            uid, prefix = args
            self.sessions = [s for s in self.sessions if not (s["user_id"] == uid and s["sid_hash"].startswith(prefix))]
            return "DELETE 1"
        if "INSERT INTO login_events" in sql:
            self.events.append(args)
            return "INSERT 1"
        if "INSERT INTO admin_audit" in sql:
            self.audit.append(args)
            return "INSERT 1"
        raise AssertionError(f"unexpected execute: {sql}")


def _client(conn: _FakeConn) -> TestClient:
    app = FastAPI()
    app.include_router(admin_router.router)
    app.dependency_overrides[require_admin] = lambda: _ADMIN
    app.dependency_overrides[get_conn] = lambda: conn
    return TestClient(app)


# ── Sessions ─────────────────────────────────────────────────────────────


def test_list_sessions_never_returns_the_full_sid():
    conn = _FakeConn(
        sessions=[
            {
                "sid_hash": "abcdefghijklmnop",
                "user_id": 7,
                "created_at": None,
                "last_seen_at": None,
                "expires_at": None,
                "user_agent": None,
                "ip": None,
            }
        ]
    )
    r = _client(conn).get("/api/admin/users/7/sessions")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["sid_prefix"] == "abcdefghijkl"
    assert "sid_hash" not in body[0]
    assert all("abcdefghijklmnop" != v for v in body[0].values())


_PREFIX = "abcdef012345"
_HASH = _PREFIX + "a" * 52


def test_revoke_session_by_unique_prefix_succeeds():
    conn = _FakeConn(sessions=[{"sid_hash": _HASH, "user_id": 7}])
    r = _client(conn).delete(f"/api/admin/users/7/sessions/{_PREFIX}", headers=_ORIGIN)
    assert r.status_code == 204
    assert conn.sessions == []
    assert len(conn.audit) == 1


def test_revoke_session_unknown_prefix_404s():
    conn = _FakeConn(sessions=[{"sid_hash": _HASH, "user_id": 7}])
    r = _client(conn).delete("/api/admin/users/7/sessions/fedcba987654", headers=_ORIGIN)
    assert r.status_code == 404
    assert r.json()["detail"] == "session not found"
    assert conn.sessions  # untouched
    assert conn.audit == []


def test_revoke_session_ambiguous_prefix_409s_and_deletes_nothing():
    conn = _FakeConn(
        sessions=[
            {"sid_hash": _PREFIX + "1" * 52, "user_id": 7},
            {"sid_hash": _PREFIX + "2" * 52, "user_id": 7},
        ]
    )
    r = _client(conn).delete(f"/api/admin/users/7/sessions/{_PREFIX}", headers=_ORIGIN)
    assert r.status_code == 409
    assert r.json()["detail"] == "prefix matches multiple sessions"
    assert len(conn.sessions) == 2
    assert conn.audit == []


def test_revoke_session_refuses_a_short_prefix_before_looking_anything_up():
    """Refused as on the self-service route, so a one-character guess cannot
    revoke whichever session it happens to be the only match for."""
    conn = _FakeConn(sessions=[{"sid_hash": _HASH, "user_id": 7}])
    r = _client(conn).delete("/api/admin/users/7/sessions/a", headers=_ORIGIN)
    assert r.status_code == 400
    assert r.json()["detail"] == "prefix too short"
    assert conn.sessions  # untouched
    assert conn.audit == []


def test_revoke_session_requires_csrf_origin():
    conn = _FakeConn(sessions=[{"sid_hash": _HASH, "user_id": 7}])
    r = _client(conn).delete(f"/api/admin/users/7/sessions/{_PREFIX}")
    assert r.status_code == 403
    assert conn.sessions  # untouched


# ── API keys ─────────────────────────────────────────────────────────────


def test_api_key_issuance_and_listing_routes_no_longer_exist():
    """No admin route issues or lists API keys any more; the path answers
    404, same as any other unregistered route."""
    conn = _FakeConn()
    client = _client(conn)
    assert client.get("/api/admin/api-keys").status_code == 404
    assert client.post("/api/admin/api-keys", json={}, headers=_ORIGIN).status_code == 404
    assert client.delete("/api/admin/api-keys/1", headers=_ORIGIN).status_code == 404
