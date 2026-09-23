"""Router-shape tests for the admin user drawer's sessions/API-keys/invites
endpoints: request validation, the exact-prefix session lookup, hash-only API
key storage, and invite-role validation.

Exercised through a minimal standalone app (not ``api.main``) with
``require_admin``/``get_conn`` overridden to a fake in-memory connection, so
this never touches a real database -- see ``tests/unit/test_admin_bounds.py``
for the same pattern.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.deps import get_conn
from api.routers import admin as admin_router
from api.security import User, require_admin, token_hash

_ADMIN = User(
    user_id=1,
    email="admin@example.com",
    name="Admin",
    avatar_url=None,
    role="admin",
    suspended_at=None,
    llm_approved=True,
)

_ORIGIN = {"Origin": "http://test"}


class _FakeConn:
    """In-memory stand-in for the tiny set of queries these endpoints issue.
    Dispatches on a recognizable substring of the SQL text, matching the
    exact queries written in ``api/routers/admin.py``."""

    def __init__(self, *, sessions=None, users=None, api_keys=None):
        self.sessions = sessions or []  # list of dict(sid_hash, user_id)
        self.users = users if users is not None else {1: "admin@example.com"}
        self.api_keys = api_keys or []  # list of dict(id, owner_user_id, revoked_at)
        self._next_api_key_id = (max((k["id"] for k in self.api_keys), default=0)) + 1
        self.events: list[tuple] = []

    async def fetch(self, sql, *args):
        if "FROM sessions WHERE user_id=$1" in sql:
            (uid,) = args
            return [
                {"sid_hash": s["sid_hash"]} | {k: v for k, v in s.items() if k != "sid_hash"}
                for s in self.sessions
                if s["user_id"] == uid
            ]
        if "FROM api_keys" in sql:
            # One query with a NULL-guarded owner filter and a row cap, so
            # the owner arrives as $1 (possibly None) and the limit as $2.
            owner, limit = args
            rows = [r for r in self.api_keys if r.get("owner_user_id") is not None]
            if owner is not None:
                rows = [r for r in rows if r["owner_user_id"] == owner]
            return rows[:limit]
        raise AssertionError(f"unexpected fetch: {sql}")

    async def fetchval(self, sql, *args):
        if "FROM users WHERE user_id=$1" in sql:
            (uid,) = args
            return 1 if uid in self.users else None
        raise AssertionError(f"unexpected fetchval: {sql}")

    async def fetchrow(self, sql, *args):
        if "INSERT INTO api_keys" in sql:
            digest, owner_user_id, tier, label, expires_at = args
            row = {
                "id": self._next_api_key_id,
                "owner_user_id": owner_user_id,
                "tier": tier,
                "label": label,
                "created_at": datetime.now(timezone.utc),
                "expires_at": expires_at,
                "revoked_at": None,
                "key_hash": digest,
            }
            self._next_api_key_id += 1
            self.api_keys.append(row)
            return {k: v for k, v in row.items() if k != "key_hash"}
        if "UPDATE api_keys SET revoked_at" in sql:
            (key_id,) = args
            for r in self.api_keys:
                if r["id"] == key_id and r.get("owner_user_id") is not None and r["revoked_at"] is None:
                    r["revoked_at"] = datetime.now(timezone.utc)
                    return {"id": r["id"], "owner_user_id": r["owner_user_id"]}
            return None
        if "INSERT INTO user_invites" in sql:
            email, role, llm_approved, _invited_by = args
            return {
                "invite_id": 1,
                "email": email,
                "role": role,
                "llm_approved": llm_approved,
                "created_at": datetime.now(timezone.utc),
                "expires_at": datetime.now(timezone.utc),
            }
        raise AssertionError(f"unexpected fetchrow: {sql}")

    async def execute(self, sql, *args):
        if "DELETE FROM sessions" in sql:
            uid, prefix = args
            self.sessions = [s for s in self.sessions if not (s["user_id"] == uid and s["sid_hash"].startswith(prefix))]
            return "DELETE 1"
        if "INSERT INTO login_events" in sql:
            self.events.append(args)
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


def test_revoke_session_by_unique_prefix_succeeds():
    conn = _FakeConn(sessions=[{"sid_hash": "abc123xxxxxx", "user_id": 7}])
    r = _client(conn).delete("/api/admin/users/7/sessions/abc123", headers=_ORIGIN)
    assert r.status_code == 204
    assert conn.sessions == []


def test_revoke_session_unknown_prefix_404s():
    conn = _FakeConn(sessions=[{"sid_hash": "abc123xxxxxx", "user_id": 7}])
    r = _client(conn).delete("/api/admin/users/7/sessions/zzz", headers=_ORIGIN)
    assert r.status_code == 404
    assert conn.sessions  # untouched


def test_revoke_session_ambiguous_prefix_404s_and_deletes_nothing():
    conn = _FakeConn(
        sessions=[
            {"sid_hash": "abc111", "user_id": 7},
            {"sid_hash": "abc222", "user_id": 7},
        ]
    )
    r = _client(conn).delete("/api/admin/users/7/sessions/abc", headers=_ORIGIN)
    assert r.status_code == 404
    assert len(conn.sessions) == 2


def test_revoke_session_requires_csrf_origin():
    conn = _FakeConn(sessions=[{"sid_hash": "abc123", "user_id": 7}])
    r = _client(conn).delete("/api/admin/users/7/sessions/abc123")
    assert r.status_code == 403
    assert conn.sessions  # untouched


# ── API keys ─────────────────────────────────────────────────────────────


def test_issue_api_key_returns_raw_key_once_and_stores_only_hash():
    conn = _FakeConn()
    r = _client(conn).post(
        "/api/admin/api-keys",
        json={"owner_user_id": 1, "tier": "pro", "label": "svc"},
        headers=_ORIGIN,
    )
    assert r.status_code == 201
    body = r.json()
    raw_key = body["key"]
    assert raw_key.startswith("sk_")
    stored = conn.api_keys[0]
    assert stored["key_hash"] == token_hash(raw_key)
    assert "key" not in stored or stored.get("key") != raw_key


def test_issue_api_key_unknown_owner_404s():
    conn = _FakeConn(users={})
    r = _client(conn).post("/api/admin/api-keys", json={"owner_user_id": 99, "tier": "pro"}, headers=_ORIGIN)
    assert r.status_code == 404
    assert conn.api_keys == []


def test_list_api_keys_never_includes_raw_key_or_hash():
    conn = _FakeConn(
        api_keys=[
            {
                "id": 1,
                "owner_user_id": 1,
                "tier": "pro",
                "label": "x",
                "created_at": None,
                "expires_at": None,
                "revoked_at": None,
                "key_hash": "deadbeef",
            }
        ]
    )
    r = _client(conn).get("/api/admin/api-keys")
    assert r.status_code == 200
    body = r.json()[0]
    assert "key" not in body
    assert "key_hash" not in body


def test_list_api_keys_excludes_legacy_operator_inserted_rows():
    """A legacy row has no ``owner_user_id`` even though 0053 backfills
    ``key_hash`` for every row, admin-issued or not -- ``owner_user_id`` is
    what now distinguishes an admin-managed key."""
    conn = _FakeConn(
        api_keys=[
            {
                "id": 1,
                "owner_user_id": None,
                "tier": "pro",
                "label": None,
                "created_at": None,
                "expires_at": None,
                "revoked_at": None,
                "key_hash": "backfilled-legacy-hash",
            }
        ]
    )
    r = _client(conn).get("/api/admin/api-keys")
    assert r.status_code == 200
    assert r.json() == []


def test_list_api_keys_filters_by_owner():
    conn = _FakeConn(
        api_keys=[
            {
                "id": 1,
                "owner_user_id": 1,
                "tier": "pro",
                "label": None,
                "created_at": None,
                "expires_at": None,
                "revoked_at": None,
                "key_hash": "a",
            },
            {
                "id": 2,
                "owner_user_id": 2,
                "tier": "pro",
                "label": None,
                "created_at": None,
                "expires_at": None,
                "revoked_at": None,
                "key_hash": "b",
            },
        ]
    )
    r = _client(conn).get("/api/admin/api-keys", params={"owner_user_id": 2})
    assert r.status_code == 200
    body = r.json()
    assert [row["id"] for row in body] == [2]


def test_revoke_api_key_sets_revoked_at():
    conn = _FakeConn(
        api_keys=[
            {
                "id": 5,
                "owner_user_id": 1,
                "tier": "pro",
                "label": None,
                "created_at": None,
                "expires_at": None,
                "revoked_at": None,
                "key_hash": "a",
            },
        ]
    )
    r = _client(conn).delete("/api/admin/api-keys/5", headers=_ORIGIN)
    assert r.status_code == 204
    assert conn.api_keys[0]["revoked_at"] is not None


def test_revoke_already_revoked_api_key_404s():
    conn = _FakeConn(
        api_keys=[
            {
                "id": 5,
                "owner_user_id": 1,
                "tier": "pro",
                "label": None,
                "created_at": None,
                "expires_at": None,
                "revoked_at": datetime.now(timezone.utc),
                "key_hash": "a",
            },
        ]
    )
    r = _client(conn).delete("/api/admin/api-keys/5", headers=_ORIGIN)
    assert r.status_code == 404


# ── Invites ──────────────────────────────────────────────────────────────


def test_create_invite_happy_path():
    conn = _FakeConn()
    r = _client(conn).post(
        "/api/admin/invites",
        json={"email": "new@example.com", "role": "admin", "llm_approved": True},
        headers=_ORIGIN,
    )
    assert r.status_code == 201
    body = r.json()
    assert body["email"] == "new@example.com"
    assert body["role"] == "admin"
    assert body["llm_approved"] is True


def test_create_invite_rejects_invalid_role():
    conn = _FakeConn()
    r = _client(conn).post("/api/admin/invites", json={"email": "x@example.com", "role": "superadmin"}, headers=_ORIGIN)
    assert r.status_code == 400
