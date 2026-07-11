"""API tests for GET /api/{agency_id}/forecast/overview."""

import os
from datetime import date

import asyncpg
import httpx
import pytest
from httpx import ASGITransport

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://localhost/transit")


@pytest.fixture
async def overview_client(apply_schema):
    from api.main import app

    pool = await asyncpg.create_pool(DATABASE_URL)
    app.state.pool = pool
    row = await pool.fetchrow(
        "INSERT INTO agencies (agency_name, feed_url) VALUES ($1, $2) RETURNING agency_id",
        "Overview Test Agency",
        "http://overview-test.example.com",
    )
    aid = row["agency_id"]
    empty_row = await pool.fetchrow(
        "INSERT INTO agencies (agency_name, feed_url) VALUES ($1, $2) RETURNING agency_id",
        "Empty Overview Agency",
        "http://overview-empty.example.com",
    )
    aid_empty = empty_row["agency_id"]
    # route_code 100 has a static_routes label; route_code 200 does not (falls back to code).
    await pool.execute(
        "INSERT INTO static_routes (agency_id, route_id, route_short_name, route_long_name) VALUES ($1, $2, $3, $4)",
        aid,
        "R(100)",
        "100",
        "Main Line",
    )
    await pool.executemany(
        "INSERT INTO agg_route_hour_dow "
        "(agency_id, route_code, service_type, dow, hour, avg_min, samples) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7)",
        [
            # route 100: Mon midday pooled (8*200 + 2*50)/250 = 6.8
            (aid, "100", "平日", 1, 12, 8.0, 200),
            (aid, "100", "平日", 1, 9, 2.0, 50),
            # route 200: huge but low-sample (4 < 30) -> excluded from worst, muted, sorted last
            (aid, "200", "平日", 3, 17, 40.0, 4),
        ],
    )
    await pool.executemany(
        "INSERT INTO agg_route_daily "
        "(agency_id, date, route_code, service_type, avg_delay_sec, worst_delay_sec, trips_observed, samples, last_seen_at) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,now())",
        [
            # latest date for route 100 is 2026-06-02 (MAX(date) anchors the
            # 7-day window at agency, not route, grain — but only route 100
            # has any agg_route_daily rows here). Window is
            # (latest-7, latest] = (2026-05-26, 2026-06-02].
            (aid, date(2026, 5, 26), "100", "平日", 600, 600, 5, 50),  # 10.0 min — latest-7, EXCLUDED
            (aid, date(2026, 5, 27), "100", "平日", 60, 60, 5, 50),  # 1.0 min — latest-6, INCLUDED (oldest in-window)
            (aid, date(2026, 6, 1), "100", "平日", 120, 180, 5, 50),  # 2.0 min
            (aid, date(2026, 6, 2), "100", "平日", 240, 300, 5, 50),  # 4.0 min
        ],
    )
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, aid, aid_empty
    async with pool.acquire() as conn:
        await conn.execute("TRUNCATE agencies, agg_route_hour_dow, agg_route_daily, static_routes CASCADE")
    await pool.close()


async def test_overview_grid_worst_and_routes(overview_client):
    client, aid, _aid_empty = overview_client
    r = await client.get(f"/api/{aid}/forecast/overview")
    assert r.status_code == 200
    body = r.json()
    assert len(body["grid"]) == 35
    # worst = route 100 Mon midday pooled 6.8; route 200 excluded (low-conf)
    assert body["worst"]["dow"] == 1
    assert body["worst"]["band"] == "midday"
    assert body["worst"]["expected_avg_min"] == pytest.approx(6.8, abs=0.05)
    by_code = {x["route_code"]: x for x in body["routes"]}
    assert by_code["100"]["route_name"] == "100"  # short_name preferred over long_name
    assert by_code["200"]["route_name"] == "200"  # no static label -> falls back to code
    assert by_code["200"]["low_confidence"] is True
    assert by_code["100"]["low_confidence"] is False
    # low-confidence route sorts last
    assert body["routes"][-1]["route_code"] == "200"
    assert body["disclaimer"]


async def test_overview_empty_agency(overview_client):
    client, _aid, aid_empty = overview_client
    # a real agency with no agg rows -> 35-cell grid, null worst, empty routes
    r = await client.get(f"/api/{aid_empty}/forecast/overview")
    assert r.status_code == 200
    body = r.json()
    assert len(body["grid"]) == 35
    assert body["worst"] is None
    assert body["routes"] == []


async def test_overview_route_recent_daily_trend(overview_client):
    client, aid, _aid_empty = overview_client
    r = await client.get(f"/api/{aid}/forecast/overview")
    assert r.status_code == 200
    body = r.json()
    by_code = {x["route_code"]: x for x in body["routes"]}
    # window is (latest-7, latest] = (2026-05-26, 2026-06-02]: 05-27 (latest-6)
    # is the oldest INCLUDED day; 05-26 (latest-7, exactly on the boundary) is
    # EXCLUDED and must not appear.
    assert by_code["100"]["recent_daily"] == pytest.approx([1.0, 2.0, 4.0], abs=0.05)
    # route 200 has no agg_route_daily rows at all -> empty, not missing/error
    assert by_code["200"]["recent_daily"] == []
