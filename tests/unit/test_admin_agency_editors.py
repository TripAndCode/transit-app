"""Standards/weights PATCH editors on the admin agency page.

Exercised through a minimal standalone app (not ``api.main``) with
``require_admin``/``get_conn`` overridden, so nothing here touches a real
pool, database, or feed -- the same pattern as
``tests/unit/test_admin_agency_actions.py``.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.deps import get_conn
from api.routers import admin_agencies
from api.routers.admin_agencies import _MAX_EDIT_ROWS
from api.security import User, require_admin

_ADMIN = User(
    user_id=7,
    email="admin@example.com",
    name="Admin",
    avatar_url=None,
    role="admin",
    suspended_at=None,
    llm_approved=True,
)


class _FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


class _EditorConn:
    """Fake asyncpg connection standing in for the standards/weights tables.

    Tracks in-memory rows so a PATCH's before/after snapshots reflect the
    edits it applied, the same way the real transaction would.
    """

    def __init__(self, *, standards: list[dict[str, Any]] | None = None, weights: list[dict[str, Any]] | None = None):
        self.standards = list(standards or [])
        self.weights = list(weights or [])
        self.audit: list[tuple[Any, ...]] = []

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any]:
        return {"agency_id": args[0] if args else 1}

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        if "route_performance_standards" in sql:
            return [dict(r) for r in self.standards]
        if "ridership_weights" in sql:
            return [dict(r) for r in self.weights]
        raise AssertionError(f"unexpected fetch: {sql}")

    async def execute(self, sql: str, *args: Any) -> str:
        if "INSERT INTO route_performance_standards" in sql:
            _agency_id, route_code, metric_type, threshold_value, bonus_malus_rate = args
            self.standards = [
                r for r in self.standards if not (r["route_code"] == route_code and r["metric_type"] == metric_type)
            ]
            self.standards.append(
                {
                    "route_code": route_code,
                    "metric_type": metric_type,
                    "threshold_value": threshold_value,
                    "bonus_malus_rate": bonus_malus_rate,
                }
            )
            return "INSERT 1"
        if "DELETE FROM route_performance_standards" in sql:
            _, route_code, metric_type = args
            self.standards = [
                r for r in self.standards if not (r["route_code"] == route_code and r["metric_type"] == metric_type)
            ]
            return "DELETE 1"
        if "INSERT INTO ridership_weights" in sql:
            # The default-weight upsert addresses no route_code column ($1, NULL, $2)
            # so it takes one fewer bind parameter than the per-route upsert.
            route_code = None if len(args) == 2 else args[1]
            weight = args[-1]
            self.weights = [r for r in self.weights if r["route_code"] != route_code]
            self.weights.append({"route_code": route_code, "weight": weight})
            return "INSERT 1"
        if "DELETE FROM ridership_weights" in sql:
            route_code = None if len(args) == 1 else args[1]
            self.weights = [r for r in self.weights if r["route_code"] != route_code]
            return "DELETE 1"
        if "INSERT INTO admin_audit" in sql:
            self.audit.append(args)
            return "INSERT 1"
        raise AssertionError(f"unexpected execute: {sql}")

    def transaction(self) -> _FakeTransaction:
        return _FakeTransaction()


def _client(conn: _EditorConn) -> TestClient:
    app = FastAPI()
    app.include_router(admin_agencies.router)
    app.dependency_overrides[require_admin] = lambda: _ADMIN
    app.dependency_overrides[get_conn] = lambda: conn
    return TestClient(app)


@pytest.fixture(autouse=True)
def _no_csrf(monkeypatch):
    monkeypatch.setattr(admin_agencies, "csrf_guard", lambda _request: None)


# ── PATCH /{agency_id}/standards ────────────────────────────────────────


def test_patch_standards_upserts_and_deletes_in_one_request():
    conn = _EditorConn(
        standards=[{"route_code": "A1", "metric_type": "ewt_sec", "threshold_value": 60.0, "bonus_malus_rate": 0.1}]
    )
    body = {
        "upsert": [{"route_code": "B2", "metric_type": "ewt_sec", "threshold_value": 90.0, "bonus_malus_rate": 0.2}],
        "delete": [{"route_code": "A1", "metric_type": "ewt_sec", "threshold_value": 60.0, "bonus_malus_rate": 0.1}],
    }
    response = _client(conn).patch("/api/admin/agencies/1/standards", json=body)

    assert response.status_code == 200
    assert response.json() == [
        {"route_code": "B2", "metric_type": "ewt_sec", "threshold_value": 90.0, "bonus_malus_rate": 0.2}
    ]


def test_patch_standards_rejects_a_batch_over_the_row_cap():
    conn = _EditorConn()
    body = {
        "upsert": [
            {"route_code": f"R{i}", "metric_type": "ewt_sec", "threshold_value": 1.0, "bonus_malus_rate": 0.0}
            for i in range(_MAX_EDIT_ROWS + 1)
        ],
        "delete": [],
    }
    response = _client(conn).patch("/api/admin/agencies/1/standards", json=body)

    assert response.status_code == 422
    assert conn.audit == []


def test_patch_standards_records_before_and_after_in_the_audit_entry():
    conn = _EditorConn(
        standards=[{"route_code": "A1", "metric_type": "ewt_sec", "threshold_value": 60.0, "bonus_malus_rate": 0.1}]
    )
    body = {
        "upsert": [{"route_code": "A1", "metric_type": "ewt_sec", "threshold_value": 75.0, "bonus_malus_rate": 0.15}],
        "delete": [],
    }
    response = _client(conn).patch("/api/admin/agencies/1/standards", json=body)

    assert response.status_code == 200
    assert len(conn.audit) == 1
    actor_id, action, target_type, target_id, before_json, after_json, _reason = conn.audit[0]
    assert actor_id == _ADMIN.user_id
    assert action == "agency.standards_updated"
    assert target_type == "agency"
    assert target_id == "1"
    assert json.loads(before_json) == [
        {"route_code": "A1", "metric_type": "ewt_sec", "threshold_value": 60.0, "bonus_malus_rate": 0.1}
    ]
    assert json.loads(after_json) == [
        {"route_code": "A1", "metric_type": "ewt_sec", "threshold_value": 75.0, "bonus_malus_rate": 0.15}
    ]


# ── PATCH /{agency_id}/weights ──────────────────────────────────────────


def test_patch_weights_upserts_and_deletes_in_one_request():
    conn = _EditorConn(weights=[{"route_code": "A1", "weight": 2.0}])
    body = {
        "upsert": [{"route_code": "B2", "weight": 3.5}],
        "delete": [{"route_code": "A1", "weight": 2.0}],
    }
    response = _client(conn).patch("/api/admin/agencies/1/weights", json=body)

    assert response.status_code == 200
    assert response.json() == [{"route_code": "B2", "weight": 3.5}]


def test_patch_weights_rejects_a_batch_over_the_row_cap():
    conn = _EditorConn()
    body = {
        "upsert": [{"route_code": f"R{i}", "weight": 1.0} for i in range(_MAX_EDIT_ROWS + 1)],
        "delete": [],
    }
    response = _client(conn).patch("/api/admin/agencies/1/weights", json=body)

    assert response.status_code == 422
    assert conn.audit == []


def test_patch_weights_null_route_code_addresses_the_agency_default():
    """``route_code: null`` upserts/deletes the agency's own default-weight
    row (a distinct, partially-indexed row per migration 0035), not a route
    literally named "null"."""
    conn = _EditorConn(weights=[{"route_code": None, "weight": 1.0}])
    body = {
        "upsert": [{"route_code": None, "weight": 4.0}],
        "delete": [],
    }
    response = _client(conn).patch("/api/admin/agencies/1/weights", json=body)

    assert response.status_code == 200
    assert response.json() == [{"route_code": None, "weight": 4.0}]


def test_patch_weights_records_before_and_after_in_the_audit_entry():
    conn = _EditorConn(weights=[{"route_code": "A1", "weight": 2.0}])
    body = {
        "upsert": [{"route_code": "A1", "weight": 5.0}],
        "delete": [],
    }
    response = _client(conn).patch("/api/admin/agencies/1/weights", json=body)

    assert response.status_code == 200
    _actor_id, action, _target_type, _target_id, before_json, after_json, _reason = conn.audit[0]
    assert action == "agency.weights_updated"
    assert json.loads(before_json) == [{"route_code": "A1", "weight": 2.0}]
    assert json.loads(after_json) == [{"route_code": "A1", "weight": 5.0}]
