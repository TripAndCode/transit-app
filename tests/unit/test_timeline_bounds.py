"""GET /delays/timeline's `date` bound.

`compute_delay_timeline` (pipeline/reports/timeline.py) is `@async_lru_cache`d
with `maxsize=16`, keyed by `(agency_id, day, step_minutes)`. Without a bound
on `day`, an anonymous, reachable caller could iterate arbitrary calendar
dates to evict every real cache entry. `timeline_day_in_range` is the pure
predicate the router applies before ever computing a frame; the HTTP-level
tests exercise it through a minimal app with every DB-touching dependency
overridden to a no-op fake, the same shape `test_map_bounds.py` uses.
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.deps import get_agency, get_ch, get_conn
from api.range import MAX_RANGE_DAYS, jst_today
from api.routers import map as map_router
from api.routers.map import timeline_day_in_range


def test_timeline_day_in_range_accepts_today():
    today = jst_today()
    assert timeline_day_in_range(today, today) is True


def test_timeline_day_in_range_rejects_a_future_day():
    today = jst_today()
    assert timeline_day_in_range(today + timedelta(days=1), today) is False


def test_timeline_day_in_range_accepts_the_oldest_allowed_day():
    today = jst_today()
    edge = today - timedelta(days=MAX_RANGE_DAYS)
    assert timeline_day_in_range(edge, today) is True


def test_timeline_day_in_range_rejects_one_day_older_than_the_window():
    today = jst_today()
    assert timeline_day_in_range(today - timedelta(days=MAX_RANGE_DAYS + 1), today) is False


class _EmptyClickHouse:
    """Reports no rows, so `max_captured_at` returns None and the default-day
    path (no `date` supplied) resolves to `jst_today()` without touching
    Postgres."""

    async def query(self, *_args, **_kwargs):
        return SimpleNamespace(result_rows=[], column_names=[])


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(map_router.router)
    app.dependency_overrides[get_agency] = lambda: 1
    app.dependency_overrides[get_conn] = lambda: None
    app.dependency_overrides[get_ch] = lambda: _EmptyClickHouse()
    return TestClient(app)


def test_delay_timeline_rejects_a_future_day():
    client = _client()
    future = (jst_today() + timedelta(days=1)).isoformat()
    response = client.get("/api/1/delays/timeline", params={"date": future})
    assert response.status_code == 422


def test_delay_timeline_rejects_a_day_older_than_the_range_window():
    client = _client()
    too_old = (jst_today() - timedelta(days=MAX_RANGE_DAYS + 1)).isoformat()
    response = client.get("/api/1/delays/timeline", params={"date": too_old})
    assert response.status_code == 422


def test_delay_timeline_still_rejects_a_malformed_date_with_400():
    client = _client()
    response = client.get("/api/1/delays/timeline", params={"date": "not-a-date"})
    assert response.status_code == 400


def test_delay_timeline_accepts_the_oldest_allowed_day():
    client = _client()
    edge = (jst_today() - timedelta(days=MAX_RANGE_DAYS)).isoformat()
    response = client.get("/api/1/delays/timeline", params={"date": edge})
    assert response.status_code == 200
