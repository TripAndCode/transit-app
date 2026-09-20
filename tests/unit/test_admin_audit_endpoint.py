"""GET /api/admin/audit request-shape validation and merge wiring:

- `limit` clamps into [1, 200] rather than trusting the caller.
- `from`/`to` reject an unparsable date with 422 instead of a 500 from a
  malformed SQL bind.
- `cursor` rejects a tampered/garbage token with 422.
- The response merges the `admin_audit` and `login_events` fetches (the
  merge itself is covered by test_admin_audit_merge.py) and only queries
  `login_events` for the `login`/`login_failed` kinds.

Exercised through a minimal standalone app (not `api.main`) with
`require_admin`/`get_conn` overridden, so this never touches a real
connection pool or database.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.deps import get_conn
from api.routers import admin as admin_router
from api.security import User, require_admin
from pipeline.query import admin_audit as aa

_ADMIN = User(
    user_id=1,
    email="admin@example.com",
    name="Admin",
    avatar_url=None,
    role="admin",
    suspended_at=None,
    llm_approved=True,
)

T0 = datetime(2026, 9, 20, 12, 0, 0, tzinfo=timezone.utc)


class _FakeConn:
    """Fake asyncpg connection routing `fetch` by which table the SQL
    targets, and recording every call for assertions."""

    def __init__(self, audit_rows=None, login_rows=None):
        self.audit_rows = audit_rows or []
        self.login_rows = login_rows or []
        self.calls: list[tuple[str, tuple]] = []

    async def fetch(self, sql, *args):
        self.calls.append((sql, args))
        if "FROM admin_audit" in sql:
            return self.audit_rows
        if "FROM login_events" in sql:
            return self.login_rows
        raise AssertionError(f"unexpected query: {sql}")


def _client(conn: _FakeConn) -> TestClient:
    app = FastAPI()
    app.include_router(admin_router.router)
    app.dependency_overrides[require_admin] = lambda: _ADMIN
    app.dependency_overrides[get_conn] = lambda: conn
    return TestClient(app)


def _audit_row(id_=1, at=T0):
    return {
        "id": id_,
        "at": at,
        "actor_id": 1,
        "action": "user.updated",
        "target_type": "user",
        "target_id": "5",
        "before": '{"role": "user"}',
        "after": '{"role": "admin"}',
        "reason": None,
        "ip": None,
    }


def _login_row(event_id=10, at=T0, kind="login", user_id=5):
    return {
        "event_id": event_id,
        "at": at,
        "actor_id": user_id,
        "user_id": user_id,
        "kind": kind,
        "meta": None,
        "ip": None,
    }


def test_returns_merged_page():
    conn = _FakeConn(audit_rows=[_audit_row()], login_rows=[_login_row()])
    resp = _client(conn).get("/api/admin/audit")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 2
    actions = {item["action"] for item in body["items"]}
    assert actions == {"user.updated", "login.ok"}
    assert body["next_cursor"] is None


def test_diff_fields_are_decoded_json():
    conn = _FakeConn(audit_rows=[_audit_row()])
    resp = _client(conn).get("/api/admin/audit")
    item = resp.json()["items"][0]
    assert item["before"] == {"role": "user"}
    assert item["after"] == {"role": "admin"}


def test_limit_is_clamped_above_200():
    conn = _FakeConn()
    resp = _client(conn).get("/api/admin/audit", params={"limit": 5000})
    assert resp.status_code == 200
    # The fetch LIMIT bound is the last positional arg on the admin_audit query.
    _sql, args = conn.calls[0]
    assert args[-1] == 201


def test_limit_is_clamped_below_1():
    conn = _FakeConn()
    resp = _client(conn).get("/api/admin/audit", params={"limit": 0})
    assert resp.status_code == 200
    _sql, args = conn.calls[0]
    assert args[-1] == 2


def test_invalid_from_date_is_422():
    conn = _FakeConn()
    resp = _client(conn).get("/api/admin/audit", params={"from": "not-a-date"})
    assert resp.status_code == 422


def test_invalid_to_date_is_422():
    conn = _FakeConn()
    resp = _client(conn).get("/api/admin/audit", params={"to": "not-a-date"})
    assert resp.status_code == 422


def test_invalid_cursor_is_422():
    conn = _FakeConn()
    resp = _client(conn).get("/api/admin/audit", params={"cursor": "garbage!!"})
    assert resp.status_code == 422


def test_valid_cursor_is_accepted():
    conn = _FakeConn()
    cursor = aa.encode_cursor({"at": T0, "source": "audit", "id": 1})
    resp = _client(conn).get("/api/admin/audit", params={"cursor": cursor})
    assert resp.status_code == 200


def test_action_filter_binds_into_the_admin_audit_query_and_login_kind_list():
    conn = _FakeConn(audit_rows=[_audit_row()], login_rows=[_login_row()])
    resp = _client(conn).get("/api/admin/audit", params={"action": "login.ok"})
    assert resp.status_code == 200
    audit_sql, audit_args = next(c for c in conn.calls if "FROM admin_audit" in c[0])
    assert "action = " in audit_sql
    assert "login.ok" in audit_args
    _login_sql, login_args = next(c for c in conn.calls if "FROM login_events" in c[0])
    # Only the kind mapping to "login.ok" is fetched, not "login_failed" too.
    assert login_args[0] == ["login"]


def test_action_filter_for_a_non_login_action_skips_login_events_query_entirely():
    conn = _FakeConn(audit_rows=[_audit_row()])
    resp = _client(conn).get("/api/admin/audit", params={"action": "user.updated"})
    assert resp.status_code == 200
    assert not any("FROM login_events" in sql for sql, _ in conn.calls)


def test_actor_filter_is_bound_as_a_query_arg():
    conn = _FakeConn()
    resp = _client(conn).get("/api/admin/audit", params={"actor": 7})
    assert resp.status_code == 200
    _sql, args = conn.calls[0]
    assert 7 in args


def test_non_admin_forbidden():
    app = FastAPI()
    app.include_router(admin_router.router)
    app.dependency_overrides[get_conn] = lambda: _FakeConn()

    def _raise():
        from fastapi import HTTPException

        raise HTTPException(403, "not admin")

    app.dependency_overrides[require_admin] = _raise
    resp = TestClient(app).get("/api/admin/audit")
    assert resp.status_code == 403
