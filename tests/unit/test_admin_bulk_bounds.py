"""PATCH /api/admin/users/bulk request-shape and self-guard bounds:

- ``ids`` is capped at 200 entries (Pydantic ``max_length``) and must be
  non-empty -- both rejected at the request-validation layer before any
  connection is touched.
- The bulk endpoint must refuse to demote (role -> non-admin) or suspend the
  calling admin, even when that admin's id is only one of many in ``ids``.
  Approving/revoking LLM access for one's own id in a batch is not blocked --
  only the two destructive-to-self transitions are.

Exercised through a minimal standalone app (not ``api.main``) with
``require_admin``/``get_conn`` overridden to caller-supplied fakes, so this
never touches a real connection pool or database -- the guard runs and
raises before the endpoint would issue any SQL.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.deps import get_conn
from api.routers import admin as admin_router
from api.security import User, require_admin
from tests.conftest import TEST_ORIGIN

_ORIGIN = {"Origin": TEST_ORIGIN}

_ADMIN = User(
    user_id=1,
    email="admin@example.com",
    name="Admin",
    avatar_url=None,
    role="admin",
    suspended_at=None,
    llm_approved=True,
)


class _UntouchedConn:
    """Fake asyncpg connection that fails the test if the endpoint ever
    tries to use it -- proof that a rejected request never reaches SQL."""

    async def fetch(self, *args, **kwargs):
        raise AssertionError("connection should not be used: request was rejected before any SQL")

    async def execute(self, *args, **kwargs):
        raise AssertionError("connection should not be used: request was rejected before any SQL")

    def transaction(self):
        raise AssertionError("connection should not be used: request was rejected before any SQL")


def _client(*, raise_server_exceptions: bool = True) -> TestClient:
    app = FastAPI()
    app.include_router(admin_router.router)
    app.dependency_overrides[require_admin] = lambda: _ADMIN
    app.dependency_overrides[get_conn] = lambda: _UntouchedConn()
    return TestClient(app, raise_server_exceptions=raise_server_exceptions)


def test_bulk_patch_rejects_more_than_200_ids():
    ids = list(range(2, 203))  # 201 ids, none of them the caller's own id (1)
    response = _client().patch(
        "/api/admin/users/bulk", json={"ids": ids, "patch": {"llm_approved": True}}, headers=_ORIGIN
    )
    assert response.status_code == 422


def test_bulk_patch_accepts_exactly_200_ids_shape():
    # 200 ids total, including the caller's own id (1) alongside a demote --
    # a self-guard 400 (not a 422) proves the request passed the length cap
    # and reached the guard, without ever needing the fake conn to serve a
    # full transaction.
    ids = [1, *list(range(2, 201))]
    assert len(ids) == 200
    response = _client().patch("/api/admin/users/bulk", json={"ids": ids, "patch": {"role": "user"}}, headers=_ORIGIN)
    assert response.status_code == 400
    assert "self" in response.json()["detail"]


def test_bulk_patch_rejects_empty_ids():
    response = _client().patch(
        "/api/admin/users/bulk", json={"ids": [], "patch": {"llm_approved": True}}, headers=_ORIGIN
    )
    assert response.status_code == 422


def test_bulk_patch_rejects_empty_patch():
    response = _client().patch("/api/admin/users/bulk", json={"ids": [2, 3], "patch": {}}, headers=_ORIGIN)
    assert response.status_code == 400
    assert "empty" in response.json()["detail"]


def test_bulk_patch_refuses_to_demote_self():
    response = _client().patch(
        "/api/admin/users/bulk",
        json={"ids": [1, 2, 3], "patch": {"role": "user"}},
        headers=_ORIGIN,
    )
    assert response.status_code == 400
    assert "self" in response.json()["detail"]


def test_bulk_patch_refuses_to_suspend_self():
    response = _client().patch(
        "/api/admin/users/bulk",
        json={"ids": [2, 1, 3], "patch": {"suspended": True}},
        headers=_ORIGIN,
    )
    assert response.status_code == 400
    assert "self" in response.json()["detail"]


def test_bulk_patch_allows_promoting_a_batch_that_includes_self():
    # role -> "admin" is never a demotion, even for the caller's own id --
    # the self-guard must let it through to the (fake, deliberately
    # DB-touching) connection rather than reject with 400.
    response = _client(raise_server_exceptions=False).patch(
        "/api/admin/users/bulk",
        json={"ids": [1, 2], "patch": {"role": "admin"}},
        headers=_ORIGIN,
    )
    assert response.status_code != 400


def test_bulk_patch_allows_approving_llm_for_a_batch_that_includes_self():
    response = _client(raise_server_exceptions=False).patch(
        "/api/admin/users/bulk",
        json={"ids": [1, 2], "patch": {"llm_approved": True}},
        headers=_ORIGIN,
    )
    assert response.status_code != 400


def test_bulk_patch_rejects_invalid_role():
    response = _client().patch(
        "/api/admin/users/bulk",
        json={"ids": [2, 3], "patch": {"role": "superuser"}},
        headers=_ORIGIN,
    )
    assert response.status_code == 400
