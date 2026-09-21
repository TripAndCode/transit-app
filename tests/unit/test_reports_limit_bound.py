"""GET /api/{agency_id}/reports/{report_type}'s ``limit`` query param
must reject values above 500 at the FastAPI layer instead of letting an
arbitrarily large ``limit`` reach ``compute_ranking``/etc.

Exercised through a minimal standalone app with every DB-touching
dependency (``get_agency``, ``get_conn``, ``get_ch``) overridden to no-op
fakes, so this never needs a real connection pool -- rejection happens at
parameter-validation time, before the endpoint body (and those deps) would
otherwise run.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.deps import get_agency, get_ch, get_conn
from api.routers import reports as reports_router


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(reports_router.router)
    app.dependency_overrides[get_agency] = lambda: 1
    app.dependency_overrides[get_conn] = lambda: None
    app.dependency_overrides[get_ch] = lambda: None
    return TestClient(app)


def test_get_report_rejects_limit_above_500():
    response = _client().get("/api/1/reports/on_time", params={"limit": 501})
    assert response.status_code == 422


def test_get_report_rejects_limit_below_1():
    response = _client().get("/api/1/reports/on_time", params={"limit": 0})
    assert response.status_code == 422
