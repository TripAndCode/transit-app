"""api/routers/map.py request-shape bounds:

- ``GET /delays/live``'s ``limit`` must reject 0/negative values -- it
  already capped the upper end at 500 but had no floor, so ``limit=0``
  (or negative) reached ClickHouse instead of 422ing at the boundary.
- The free-text route identifiers (``route`` on ``/route-shape``,
  ``route_code`` on the ``/today/route/{route_code}/...`` paths) must be
  bounded the same way ``trip_id`` already is (``min_length=1,
  max_length=300``) so an empty or arbitrarily long value can't reach a
  ClickHouse/Postgres query unbounded.

Exercised through a minimal standalone app with every DB-touching
dependency overridden to no-op fakes -- rejection happens at
parameter-validation time, before the endpoint body (and those deps) would
otherwise run.
"""

from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.deps import get_agency, get_ch, get_conn
from api.range import get_range_ctx
from api.routers import map as map_router


class _EmptyClickHouse:
    """Fake ClickHouse client whose every query reports no rows, so
    ``max_captured_at`` returns ``None`` and ``live_delays`` takes its
    early-return path without touching Postgres."""

    async def query(self, *_args, **_kwargs):
        return SimpleNamespace(result_rows=[], column_names=[])


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(map_router.router)
    app.dependency_overrides[get_agency] = lambda: 1
    app.dependency_overrides[get_conn] = lambda: None
    app.dependency_overrides[get_ch] = lambda: _EmptyClickHouse()
    return app, TestClient(app)


def test_live_delays_rejects_zero_limit():
    _, client = _client()
    response = client.get("/api/1/delays/live", params={"limit": 0})
    assert response.status_code == 422


def test_live_delays_rejects_negative_limit():
    _, client = _client()
    response = client.get("/api/1/delays/live", params={"limit": -1})
    assert response.status_code == 422


def test_live_delays_accepts_limit_one():
    _, client = _client()
    response = client.get("/api/1/delays/live", params={"limit": 1})
    assert response.status_code == 200


def test_route_shape_rejects_empty_route():
    app, client = _client()
    app.dependency_overrides[get_range_ctx] = lambda: None
    response = client.get("/api/1/route-shape", params={"route": ""})
    assert response.status_code == 422


def test_route_shape_rejects_route_over_300_chars():
    app, client = _client()
    app.dependency_overrides[get_range_ctx] = lambda: None
    response = client.get("/api/1/route-shape", params={"route": "x" * 301})
    assert response.status_code == 422


def test_route_trips_rejects_empty_route_code():
    _, client = _client()
    response = client.get("/api/1/today/route//trips")
    assert response.status_code in (404, 422)


def test_route_trips_rejects_route_code_over_300_chars():
    _, client = _client()
    response = client.get(f"/api/1/today/route/{'x' * 301}/trips")
    assert response.status_code == 422


def test_route_stop_profile_rejects_route_code_over_300_chars():
    _, client = _client()
    response = client.get(f"/api/1/today/route/{'x' * 301}/stop-profile")
    assert response.status_code == 422
