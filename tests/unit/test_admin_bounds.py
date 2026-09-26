"""GET /api/admin/users request-shape bounds:

- ``offset`` must reject negative values at the FastAPI parameter layer
  instead of silently feeding a negative OFFSET to Postgres.
- The ``q`` substring filter must escape LIKE metacharacters (``%``, ``_``,
  ``\\``) before interpolating into the ILIKE pattern, and the query must
  pair that with ``ESCAPE '\\'`` -- otherwise a caller-supplied ``%``/``_``
  changes the match semantics instead of being treated as a literal
  character.

Exercised through a minimal standalone app (not ``api.main``) with
``require_admin``/``get_conn`` overridden to caller-supplied fakes, so this
never touches a real connection pool or database.
"""

from __future__ import annotations

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


class _RecordingConn:
    """Fake asyncpg connection that records every SQL + args pair it sees."""

    def __init__(self):
        self.calls: list[tuple[str, tuple]] = []

    async def fetchval(self, sql, *args):
        self.calls.append((sql, args))
        return 0

    async def fetch(self, sql, *args):
        self.calls.append((sql, args))
        return []


def _client(conn: _RecordingConn) -> TestClient:
    app = FastAPI()
    app.include_router(admin_router.router)
    app.dependency_overrides[require_admin] = lambda: _ADMIN
    app.dependency_overrides[get_conn] = lambda: conn
    return TestClient(app)


def test_list_users_rejects_negative_offset():
    response = _client(_RecordingConn()).get("/api/admin/users", params={"offset": -1})
    assert response.status_code == 422


def test_list_users_accepts_zero_offset():
    response = _client(_RecordingConn()).get("/api/admin/users", params={"offset": 0})
    assert response.status_code == 200


def test_list_users_escapes_percent_in_q_and_adds_escape_clause():
    conn = _RecordingConn()
    response = _client(conn).get("/api/admin/users", params={"q": "50%_off"})
    assert response.status_code == 200
    sql, args = conn.calls[0]
    assert r"50\%\_off" in args[0]
    assert "ESCAPE '\\'" in sql


def test_list_users_filters_on_llm_approved_so_total_counts_the_waiting():
    """The nav badge asks for a count, not a page: it reads ``total`` from a
    one-row request, which is only exact if the filter reaches the SQL."""
    conn = _RecordingConn()
    response = _client(conn).get("/api/admin/users", params={"llm_approved": "false", "suspended": "false", "limit": 1})
    assert response.status_code == 200
    count_sql, _ = conn.calls[0]
    assert "NOT llm_approved" in count_sql
    assert "suspended_at IS NULL" in count_sql


def test_list_users_leaves_llm_approved_unfiltered_when_it_is_not_asked_for():
    conn = _RecordingConn()
    assert _client(conn).get("/api/admin/users").status_code == 200
    count_sql, _ = conn.calls[0]
    assert "llm_approved" not in count_sql
