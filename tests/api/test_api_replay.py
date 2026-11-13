"""Tests for GET /api/agencies/{agency_id}/replay -- the Bus Town replay feed.

ClickHouse-gated: the endpoint reads raw `updates`, so every case needs
`make ch-test` / `RUN_CH_INTEGRATION=1` (via the `ch_async_client`/`ch_client`
fixtures). Window validation and the row -> columnar-response transform are
covered without a database in `tests/unit/test_replay.py`.
"""

from datetime import date, datetime
from zoneinfo import ZoneInfo

import httpx
import pytest
from httpx import ASGITransport

from api.middleware.ratelimit import limiter
from tests.conftest import _test_pool

_DAY = date(2026, 6, 9)
_JST = ZoneInfo("Asia/Tokyo")
_UPDATE_COLUMNS = [
    "agency_id",
    "file_name",
    "captured_at",
    "trip_id",
    "service_type",
    "scheduled_time",
    "route_code",
    "stop_id",
    "stop_sequence",
    "dep_delay",
    "scheduled_sec",
]


@pytest.fixture(autouse=True)
def _reset_limiter():
    limiter.reset()
    yield


@pytest.fixture
async def replay_app(apply_schema, ch_async_client, ch_client):
    from api.main import app

    pool = await _test_pool()
    app.state.pool = pool
    app.state.ch_client = ch_async_client
    row = await pool.fetchrow(
        "INSERT INTO agencies (agency_name, feed_url) VALUES ($1, $2) RETURNING agency_id",
        "Replay Test Agency",
        "http://replay-test.example.com",
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
    await pool.execute(
        "INSERT INTO static_routes (agency_id, route_id, route_short_name) VALUES ($1, 'R1', 'みなと線')",
        agency_id,
    )
    yield app, agency_id, ch_client
    async with pool.acquire() as conn:
        await conn.execute("TRUNCATE agencies, static_stops, static_routes, agg_route_daily CASCADE")
    await pool.close()
    ch_client.command("TRUNCATE TABLE IF EXISTS updates")
    ch_client.command("TRUNCATE TABLE IF EXISTS updates_live")


@pytest.fixture
async def replay_client(replay_app):
    app, agency_id, ch_client = replay_app
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, agency_id, ch_client


def _row(
    agency_id, trip_id, stop_sequence, scheduled_sec, delay, *, route_code="R1", stop_id=None, day=_DAY, file_suffix=0
):
    clock = f"{scheduled_sec // 3600:02d}:{scheduled_sec % 3600 // 60:02d}:00"
    return (
        agency_id,
        f"f/{trip_id}-{stop_sequence}-{file_suffix}.pb",
        datetime(day.year, day.month, day.day, 7, 50, file_suffix, tzinfo=_JST),
        trip_id,
        "平日",
        clock,
        route_code,
        stop_id or f"S{stop_sequence}",
        stop_sequence,
        delay,
        scheduled_sec,
    )


async def _seed_agg_route_daily(pool, agency_id, day=_DAY, route_code="R1"):
    await pool.execute(
        "INSERT INTO agg_route_daily (agency_id, date, route_code, service_type, "
        "avg_delay_sec, worst_delay_sec, trips_observed, samples, last_seen_at, sum_delay_sec) "
        "VALUES ($1, $2, $3, 'weekday', 60, 60, 1, 1, NOW(), 60)",
        agency_id,
        day,
        route_code,
    )


@pytest.mark.asyncio
async def test_replay_unknown_agency_is_denied(replay_client):
    client, _agency_id, _ch = replay_client
    resp = await client.get("/api/agencies/999999/replay")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_replay_defaults_to_the_latest_date_with_observations(replay_app, replay_client):
    app, agency_id, ch_client = replay_app
    client, _, _ = replay_client
    await _seed_agg_route_daily(app.state.pool, agency_id)
    ch_client.insert(
        "updates",
        [_row(agency_id, "T1", 1, 8 * 3600, 60)],
        column_names=_UPDATE_COLUMNS,
    )
    resp = await client.get(f"/api/agencies/{agency_id}/replay")
    assert resp.status_code == 200
    body = resp.json()
    assert body["date"] == _DAY.isoformat()
    assert body["from_hour"] is not None and body["to_hour"] is not None
    assert body["to_hour"] - body["from_hour"] <= 3
    assert [t["trip_id"] for t in body["trips"]] == ["T1"]
    assert body["available_dates"] == [_DAY.isoformat()]
    assert body["routes"] == [{"code": "R1", "name": "みなと線", "color": None}]


@pytest.mark.asyncio
async def test_replay_window_clips_a_trip_to_the_requested_hours(replay_app, replay_client):
    app, agency_id, ch_client = replay_app
    client, _, _ = replay_client
    await _seed_agg_route_daily(app.state.pool, agency_id)
    # One trip with a stop inside [8,11) and one well outside it.
    ch_client.insert(
        "updates",
        [
            _row(agency_id, "T1", 1, 8 * 3600 + 300, 30, file_suffix=0),
            _row(agency_id, "T1", 2, 14 * 3600, 90, file_suffix=1),
        ],
        column_names=_UPDATE_COLUMNS,
    )
    resp = await client.get(f"/api/agencies/{agency_id}/replay?date={_DAY.isoformat()}&from_hour=8&to_hour=11")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["trips"]) == 1
    trip = body["trips"][0]
    assert trip["trip_id"] == "T1"
    assert trip["t"] == [8 * 3600 + 300 + 30]
    assert trip["delay"] == [30]
    assert len(body["stops"]["id"]) == 1


@pytest.mark.asyncio
async def test_replay_omits_a_stop_the_feed_never_reported(replay_app, replay_client):
    app, agency_id, ch_client = replay_app
    client, _, _ = replay_client
    await _seed_agg_route_daily(app.state.pool, agency_id)
    # stop_sequence 2 is never captured for T1 -- the feed sent no update
    # for it, so it is simply absent, not synthesized with a null entry.
    ch_client.insert(
        "updates",
        [
            _row(agency_id, "T1", 1, 8 * 3600, 0, file_suffix=0),
            _row(agency_id, "T1", 3, 8 * 3600 + 600, 15, file_suffix=1),
        ],
        column_names=_UPDATE_COLUMNS,
    )
    resp = await client.get(f"/api/agencies/{agency_id}/replay?date={_DAY.isoformat()}&from_hour=8&to_hour=11")
    assert resp.status_code == 200
    trip = resp.json()["trips"][0]
    assert len(trip["stop_idx"]) == 2
    assert trip["delay"] == [0, 15]


@pytest.mark.asyncio
async def test_replay_rejects_a_malformed_date(replay_client):
    client, agency_id, _ = replay_client
    resp = await client.get(f"/api/agencies/{agency_id}/replay?date=not-a-date")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_replay_rejects_a_window_wider_than_three_hours(replay_client):
    client, agency_id, _ = replay_client
    resp = await client.get(f"/api/agencies/{agency_id}/replay?date={_DAY.isoformat()}&from_hour=8&to_hour=13")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_replay_empty_when_no_data(replay_client):
    client, agency_id, _ = replay_client
    resp = await client.get(f"/api/agencies/{agency_id}/replay")
    assert resp.status_code == 200
    body = resp.json()
    assert body["date"] is None
    assert body["trips"] == []
    assert body["available_dates"] == []
