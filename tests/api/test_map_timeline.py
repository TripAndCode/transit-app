"""Tests for GET /api/{agency_id}/delays/timeline — the day-playback rail's feed.

ClickHouse-gated: the endpoint reads raw `updates`, so every case here needs
`make ch-test` / `RUN_CH_INTEGRATION=1` (via the `ch_async_client` fixture).
The pure bucketing/folding logic and the rendered SQL are covered without a
database in `tests/unit/test_delay_timeline.py`.
"""

from datetime import date, datetime, timezone

import httpx
import pytest
from httpx import ASGITransport

from api.middleware.ratelimit import limiter
from pipeline import cache
from tests.conftest import _test_pool

_DAY = date(2026, 3, 4)
_UPDATE_COLUMNS = [
    "agency_id",
    "file_name",
    "captured_at",
    "trip_id",
    "service_type",
    "scheduled_time",
    "route_code",
    "stop_sequence",
    "dep_delay",
    "scheduled_sec",
]


@pytest.fixture(autouse=True)
def _reset_limiter_and_cache():
    limiter.reset()
    # compute_delay_timeline is LRU-cached per (agency, day, step); a stale
    # entry from a previous case would hide whatever this one seeded.
    cache.clear_all()
    yield
    cache.clear_all()


@pytest.fixture
async def timeline_app(apply_schema, ch_async_client, ch_client):
    from api.main import app

    pool = await _test_pool()
    app.state.pool = pool
    app.state.ch_client = ch_async_client
    row = await pool.fetchrow(
        "INSERT INTO agencies (agency_name, feed_url) VALUES ($1, $2) RETURNING agency_id",
        "Timeline Test Agency",
        "http://timeline-test.example.com",
    )
    agency_id = row["agency_id"]
    for stop_id, name, lat, lon in [("S1", "駅前", 40.80, 140.70), ("S2", "市役所", 40.81, 140.71)]:
        await pool.execute(
            "INSERT INTO static_stops (agency_id, stop_id, stop_name, stop_lat, stop_lon, geom) "
            "VALUES ($1, $2, $3, $4, $5, ST_SetSRID(ST_MakePoint($5, $4), 4326))",
            agency_id,
            stop_id,
            name,
            lat,
            lon,
        )
    yield app, agency_id, ch_client
    async with pool.acquire() as conn:
        await conn.execute("TRUNCATE agencies, static_stops, static_stop_times CASCADE")
    await pool.close()
    ch_client.command("TRUNCATE TABLE IF EXISTS updates")


@pytest.fixture
async def timeline_client(timeline_app):
    app, agency_id, ch_client = timeline_app
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, agency_id, ch_client


async def _seed_visit(pool_exec, ch_client, agency_id, *, trip_id, stop_sequence, stop_id, scheduled_sec, delays):
    """One trip visit: the static mapping plus `delays` observations of it."""
    await pool_exec(
        "INSERT INTO static_stop_times (agency_id, trip_id, stop_sequence, stop_id, departure_time) "
        "VALUES ($1, $2, $3, $4, $5) ON CONFLICT DO NOTHING",
        agency_id,
        trip_id,
        stop_sequence,
        stop_id,
        f"{scheduled_sec // 3600:02d}:{scheduled_sec % 3600 // 60:02d}:00",
    )
    rows = []
    for i, delay in enumerate(delays):
        rows.append(
            (
                agency_id,
                f"f/{trip_id}-{stop_sequence}-{i}.pb",
                datetime(_DAY.year, _DAY.month, _DAY.day, 3, 0, i, tzinfo=timezone.utc),
                trip_id,
                "平日",
                f"{scheduled_sec // 3600:02d}:{scheduled_sec % 3600 // 60:02d}:00",
                "R1",
                stop_sequence,
                delay,
                scheduled_sec,
            )
        )
    ch_client.insert("updates", rows, column_names=_UPDATE_COLUMNS)


@pytest.mark.asyncio
async def test_timeline_returns_a_dense_frame_per_hour(timeline_client):
    client, agency_id, _ = timeline_client
    resp = await client.get(f"/api/{agency_id}/delays/timeline?date={_DAY.isoformat()}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["date"] == _DAY.isoformat()
    assert body["step_minutes"] == 60
    assert [f["t"] for f in body["frames"]][:2] == ["05:00", "06:00"]
    assert [f["t"] for f in body["frames"]][-1] == "23:00"
    assert len(body["frames"]) == 19
    assert all(f["points"] == [] for f in body["frames"])


@pytest.mark.asyncio
async def test_timeline_places_an_observed_stop_in_its_scheduled_bucket(timeline_client, timeline_app):
    client, agency_id, ch_client = timeline_client
    app, _, _ = timeline_app
    pool = app.state.pool
    # Three separate trips calling S1 at 08:xx, so the stop clears the
    # three-sample floor for the 08:00 bucket.
    for n, sched in enumerate([8 * 3600, 8 * 3600 + 900, 8 * 3600 + 1800]):
        await _seed_visit(
            pool.execute,
            ch_client,
            agency_id,
            trip_id=f"T{n}",
            stop_sequence=1,
            stop_id="S1",
            scheduled_sec=sched,
            delays=[120],
        )

    resp = await client.get(f"/api/{agency_id}/delays/timeline?date={_DAY.isoformat()}")
    frames = {f["t"]: f for f in resp.json()["frames"]}
    assert [p["stop_id"] for p in frames["08:00"]["points"]] == ["S1"]
    point = frames["08:00"]["points"][0]
    assert point["avg_delay_min"] == 2.0
    assert point["samples"] == 3
    assert point["lon"] == 140.7 and point["lat"] == 40.8
    assert frames["08:00"]["mean_delay_min"] == 2.0
    assert frames["09:00"]["points"] == []


@pytest.mark.asyncio
async def test_timeline_drops_a_stop_below_the_sample_floor(timeline_client, timeline_app):
    client, agency_id, ch_client = timeline_client
    pool = timeline_app[0].state.pool
    # Two trips only — one short of MIN_BUCKET_SAMPLES.
    for n, sched in enumerate([8 * 3600, 8 * 3600 + 900]):
        await _seed_visit(
            pool.execute,
            ch_client,
            agency_id,
            trip_id=f"U{n}",
            stop_sequence=1,
            stop_id="S2",
            scheduled_sec=sched,
            delays=[300],
        )
    resp = await client.get(f"/api/{agency_id}/delays/timeline?date={_DAY.isoformat()}")
    frames = {f["t"]: f for f in resp.json()["frames"]}
    assert frames["08:00"]["points"] == []
    assert frames["08:00"]["samples"] == 0


@pytest.mark.asyncio
async def test_timeline_step_15_splits_the_window_into_quarter_hours(timeline_client):
    client, agency_id, _ = timeline_client
    resp = await client.get(f"/api/{agency_id}/delays/timeline?date={_DAY.isoformat()}&step=15")
    body = resp.json()
    assert body["step_minutes"] == 15
    assert len(body["frames"]) == 76
    assert [f["t"] for f in body["frames"]][:3] == ["05:00", "05:15", "05:30"]


@pytest.mark.asyncio
async def test_timeline_rejects_an_unsupported_step(timeline_client):
    client, agency_id, _ = timeline_client
    resp = await client.get(f"/api/{agency_id}/delays/timeline?date={_DAY.isoformat()}&step=30")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_timeline_rejects_a_malformed_date(timeline_client):
    client, agency_id, _ = timeline_client
    resp = await client.get(f"/api/{agency_id}/delays/timeline?date=not-a-date")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_timeline_without_a_date_resolves_the_latest_observed_day(timeline_client, timeline_app):
    client, agency_id, ch_client = timeline_client
    pool = timeline_app[0].state.pool
    await _seed_visit(
        pool.execute,
        ch_client,
        agency_id,
        trip_id="V0",
        stop_sequence=1,
        stop_id="S1",
        scheduled_sec=8 * 3600,
        delays=[60],
    )
    resp = await client.get(f"/api/{agency_id}/delays/timeline")
    # captured_at is 03:00 UTC on _DAY, i.e. noon JST on the same civil day.
    assert resp.json()["date"] == _DAY.isoformat()
