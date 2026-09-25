"""The standards/weights editors must issue one delete + one upsert per
request regardless of how many rows the payload touches, not a per-row
``execute`` loop.

Exercised through a minimal standalone app (not ``api.main``) with
``require_admin``/``get_conn`` overridden to a fake in-memory connection --
the same pattern as ``tests/unit/test_admin_agency_actions.py`` -- with the
fake actually applying each statement to its in-memory tables so behavior
(not just call count) is checked.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.deps import get_conn
from api.routers import admin_agencies
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

_ORIGIN = {"Origin": "http://test"}

_AGENCY_ROW = {
    "agency_id": 1,
    "agency_name": "Hokuriku",
    "feed_url": "https://example.test/feed.pb",
    "static_url": None,
    "ingest_strategy": "direct_url",
    "deleted_at": None,
    "analyzed_at": None,
    "max_updates_captured_at": None,
    "latest_data_date": None,
}


class _EditorConn:
    def __init__(self, *, standards=None, weights=None):
        self.standards: list[dict[str, Any]] = list(standards or [])
        self.weights: list[dict[str, Any]] = list(weights or [])
        self.execute_calls: list[tuple[str, tuple]] = []
        self.audit: list[tuple] = []

    def transaction(self):
        @asynccontextmanager
        async def _noop():
            yield

        return _noop()

    async def fetchrow(self, sql, *_args):
        if "FROM agencies a" in sql:
            return _AGENCY_ROW
        raise AssertionError(f"unexpected fetchrow: {sql}")

    async def fetch(self, sql, *_args):
        stripped = sql.strip()
        if stripped.startswith("SELECT") and "FROM route_performance_standards" in stripped:
            return [dict(r) for r in self.standards]
        if stripped.startswith("SELECT") and "FROM ridership_weights" in stripped:
            return [dict(r) for r in self.weights]
        raise AssertionError(f"unexpected fetch: {sql}")

    async def execute(self, sql, *args):
        self.execute_calls.append((sql, args))
        stripped = sql.strip()

        if stripped.startswith("DELETE FROM route_performance_standards"):
            _agency_id, codes, metrics = args
            doomed = set(zip(codes, metrics, strict=True))
            self.standards = [s for s in self.standards if (s["route_code"], s["metric_type"]) not in doomed]
        elif stripped.startswith("INSERT INTO route_performance_standards"):
            _agency_id, codes, metrics, thresholds, rates = args
            for code, metric, threshold, rate in zip(codes, metrics, thresholds, rates, strict=True):
                self.standards = [
                    s for s in self.standards if not (s["route_code"] == code and s["metric_type"] == metric)
                ]
                self.standards.append(
                    {"route_code": code, "metric_type": metric, "threshold_value": threshold, "bonus_malus_rate": rate}
                )
        elif stripped.startswith("DELETE FROM ridership_weights"):
            _agency_id, codes = args
            self.weights = [w for w in self.weights if w["route_code"] not in codes]
        elif stripped.startswith("INSERT INTO ridership_weights") and "unnest" in stripped:
            _agency_id, codes, weights = args
            for code, weight in zip(codes, weights, strict=True):
                self.weights = [w for w in self.weights if w["route_code"] != code]
                self.weights.append({"route_code": code, "weight": weight})
        elif stripped.startswith("INSERT INTO ridership_weights") and "VALUES ($1, NULL, $2)" in stripped:
            _agency_id, weight = args
            self.weights = [w for w in self.weights if w["route_code"] is not None]
            self.weights.append({"route_code": None, "weight": weight})
        elif stripped.startswith("INSERT INTO admin_audit"):
            self.audit.append(args)
        else:
            raise AssertionError(f"unexpected execute: {sql}")
        return "OK"


def _client(conn: _EditorConn) -> TestClient:
    app = FastAPI()
    app.include_router(admin_agencies.router)
    app.dependency_overrides[require_admin] = lambda: _ADMIN
    app.dependency_overrides[get_conn] = lambda: conn
    return TestClient(app)


@pytest.fixture(autouse=True)
def _no_csrf(monkeypatch):
    monkeypatch.setattr(admin_agencies, "csrf_guard", lambda _request: None)


def test_patch_standards_issues_one_delete_and_one_upsert_for_many_rows():
    conn = _EditorConn(
        standards=[
            {"route_code": "R1", "metric_type": "ewt_sec", "threshold_value": 100.0, "bonus_malus_rate": 1.0},
            {"route_code": "R2", "metric_type": "ewt_sec", "threshold_value": 100.0, "bonus_malus_rate": 1.0},
        ]
    )
    body = {
        "upsert": [
            {"route_code": "R3", "metric_type": "ewt_sec", "threshold_value": 50.0, "bonus_malus_rate": 2.0},
            {"route_code": "R4", "metric_type": "ewt_sec", "threshold_value": 60.0, "bonus_malus_rate": 3.0},
        ],
        "delete": [
            {"route_code": "R1", "metric_type": "ewt_sec", "threshold_value": 0.0, "bonus_malus_rate": 0.0},
            {"route_code": "R2", "metric_type": "ewt_sec", "threshold_value": 0.0, "bonus_malus_rate": 0.0},
        ],
    }
    r = _client(conn).patch("/api/admin/agencies/1/standards", json=body, headers=_ORIGIN)
    assert r.status_code == 200

    write_calls = [c for c in conn.execute_calls if "admin_audit" not in c[0]]
    assert len(write_calls) == 2, f"expected exactly one delete + one upsert, got {len(write_calls)}: {write_calls}"

    codes = {s["route_code"] for s in conn.standards}
    assert codes == {"R3", "R4"}


def test_patch_weights_batches_route_rows_and_handles_the_default_row_separately():
    conn = _EditorConn(
        weights=[
            {"route_code": "R1", "weight": 1.0},
            {"route_code": None, "weight": 2.0},
        ]
    )
    body = {
        "upsert": [
            {"route_code": "R2", "weight": 5.0},
            {"route_code": "R3", "weight": 6.0},
            {"route_code": None, "weight": 9.0},
        ],
        "delete": [
            {"route_code": "R1", "weight": 1.0},
        ],
    }
    r = _client(conn).patch("/api/admin/agencies/1/weights", json=body, headers=_ORIGIN)
    assert r.status_code == 200

    write_calls = [c for c in conn.execute_calls if "admin_audit" not in c[0]]
    # One batched delete, one batched route upsert, one single-row default
    # upsert (ridership_weights' two partial-unique arbiters can't share one
    # ON CONFLICT statement) -- still O(1) in the route count, not one call
    # per row.
    assert len(write_calls) == 3, (
        f"expected delete + route-upsert + default-upsert, got {len(write_calls)}: {write_calls}"
    )

    by_code = {w["route_code"]: w["weight"] for w in conn.weights}
    assert by_code == {"R2": 5.0, "R3": 6.0, None: 9.0}


def test_patch_weights_default_delete_is_included_in_the_one_batched_delete():
    conn = _EditorConn(weights=[{"route_code": None, "weight": 2.0}, {"route_code": "R1", "weight": 1.0}])
    body = {"upsert": [], "delete": [{"route_code": None, "weight": 1.0}, {"route_code": "R1", "weight": 1.0}]}
    r = _client(conn).patch("/api/admin/agencies/1/weights", json=body, headers=_ORIGIN)
    assert r.status_code == 200

    write_calls = [c for c in conn.execute_calls if "admin_audit" not in c[0]]
    assert len(write_calls) == 1, f"expected exactly one delete call, got {len(write_calls)}: {write_calls}"
    assert conn.weights == []
