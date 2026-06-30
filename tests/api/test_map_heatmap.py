"""Tests for GET /api/{agency_id}/delays/heatmap — p90_delay_min field."""
import os
from datetime import date, datetime, time, timedelta, timezone

import asyncpg
import httpx
import pytest
from httpx import ASGITransport

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://localhost/transit")


@pytest.fixture
async def hmap_client(apply_schema):
    from api.main import app

    pool = await asyncpg.create_pool(DATABASE_URL)
    app.state.pool = pool
    row = await pool.fetchrow(
        "INSERT INTO agencies (agency_name, feed_url) VALUES ($1, $2) RETURNING agency_id",
        "HmapP90Agency",
        "http://hmap-p90-test.example.com",
    )
    aid = row["agency_id"]
    # Insert a stop with coords
    await pool.execute(
        "INSERT INTO static_stops (agency_id, stop_id, stop_name, stop_lat, stop_lon, geom) "
        "VALUES ($1, $2, $3, $4, $5, ST_SetSRID(ST_MakePoint($5,$4),4326))",
        aid, "S1", "駅前", 40.7, 140.7,
    )
    today = date.today()
    # Three daily rows with delay_sum/samples giving per-day avgs: 60s, 120s, 600s
    # p90 over these 3 days ≈ PERCENTILE_CONT(0.9) of [1, 2, 10] min = 10*0.9=9+ → 9.0 min
    for d_offset, (ds, s) in enumerate([(60, 1), (120, 1), (600, 1)]):
        await pool.execute(
            "INSERT INTO agg_stop_daily "
            "(agency_id, stop_id, date, service_type, time_band, delay_sum, samples) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7) "
            "ON CONFLICT DO NOTHING",
            aid, "S1", today - timedelta(days=d_offset), "平日", "朝", ds, s,
        )
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c, aid
    async with pool.acquire() as conn:
        await conn.execute("TRUNCATE agencies CASCADE")
    await pool.close()


@pytest.mark.asyncio
async def test_heatmap_returns_p90_delay_min(hmap_client):
    client, aid = hmap_client
    r = await client.get(f"/api/{aid}/delays/heatmap")
    assert r.status_code == 200
    features = r.json()["features"]
    assert len(features) == 1
    props = features[0]["properties"]
    assert "p90_delay_min" in props
    assert props["p90_delay_min"] is not None
    # avg is (60+120+600)/3/60 = 4.0 min; p90 is >= avg
    assert props["p90_delay_min"] >= props["avg_delay_min"]


@pytest.mark.asyncio
async def test_heatmap_p90_null_when_no_data(hmap_client):
    client, aid = hmap_client
    # Request a future date range with no data
    r = await client.get(f"/api/{aid}/delays/heatmap?from=2099-01-01&to=2099-01-07")
    assert r.status_code == 200
    # No features expected (no data in that range)
    assert r.json()["features"] == []


@pytest.fixture
async def stop_profile_client(apply_schema):
    from api.main import app

    pool = await asyncpg.create_pool(DATABASE_URL)
    app.state.pool = pool
    row = await pool.fetchrow(
        "INSERT INTO agencies (agency_name, feed_url) VALUES ($1, $2) RETURNING agency_id",
        "StopCohortAgency", "http://stop-cohort-test.example.com",
    )
    aid = row["agency_id"]

    # Two static stops
    for stop_id, name, lat, lon in [("S1", "駅前", 40.7, 140.7), ("S2", "市役所", 40.8, 140.8)]:
        await pool.execute(
            "INSERT INTO static_stops (agency_id, stop_id, stop_name, stop_lat, stop_lon, geom) "
            "VALUES ($1,$2,$3,$4,$5,ST_SetSRID(ST_MakePoint($5,$4),4326))",
            aid, stop_id, name, lat, lon,
        )
    # Route K31 trips: stop_seq 1→S1, 2→S2
    await pool.execute(
        "INSERT INTO static_trips (agency_id, trip_id, route_id, service_id) VALUES ($1,$2,$3,$4)",
        aid, "T1", "K31", "WD",
    )
    for seq, stop_id in [(1, "S1"), (2, "S2")]:
        await pool.execute(
            "INSERT INTO static_stop_times (agency_id, trip_id, stop_sequence, stop_id) "
            "VALUES ($1,$2,$3,$4)",
            aid, "T1", seq, stop_id,
        )
    # Raw updates for today (K31 with big delay at S1)
    today = date.today()
    jst = timezone(timedelta(hours=9))
    ts = datetime(today.year, today.month, today.day, 9, 0, 0, tzinfo=jst)
    await pool.execute(
        "INSERT INTO updates (agency_id, file_name, captured_at, trip_id, service_type, "
        "scheduled_time, route_code, stop_sequence, dep_delay) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)",
        aid, "f1.pb", ts, "T1", "平日", time(9, 0), "K31", 1, 600,
    )
    await pool.execute(
        "INSERT INTO updates (agency_id, file_name, captured_at, trip_id, service_type, "
        "scheduled_time, route_code, stop_sequence, dep_delay) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)",
        aid, "f1.pb", ts, "T1", "平日", time(9, 0), "K31", 2, 60,
    )
    # agg_route_stop_daily: S1 has two routes — K31 (600s avg) and K99 (200s avg)
    for route_code, ds, s in [("K31", 600, 1), ("K99", 200, 1)]:
        await pool.execute(
            "INSERT INTO agg_route_stop_daily "
            "(agency_id, route_code, stop_id, date, service_type, time_band, delay_sum, samples) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8) ON CONFLICT DO NOTHING",
            aid, route_code, "S1", today, "平日", "朝", ds, s,
        )
    # S2 only has K31 — cohort_route_count=1, so never outlier
    await pool.execute(
        "INSERT INTO agg_route_stop_daily "
        "(agency_id, route_code, stop_id, date, service_type, time_band, delay_sum, samples) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8) ON CONFLICT DO NOTHING",
        aid, "K31", "S2", today, "平日", "朝", 60, 1,
    )
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c, aid
    async with pool.acquire() as conn:
        await conn.execute("TRUNCATE agencies CASCADE")
    await pool.close()


@pytest.mark.asyncio
async def test_stop_profile_includes_cohort_fields(stop_profile_client):
    client, aid = stop_profile_client
    r = await client.get(f"/api/{aid}/today/route/K31/stop-profile")
    assert r.status_code == 200
    stops = r.json()["stops"]
    s1 = next(s for s in stops if s["stop_sequence"] == 1)
    assert "cohort_avg_delay_sec" in s1
    assert "cohort_route_count" in s1
    assert "is_outlier" in s1
    # S1: K31 avg=600s, cohort avg=(600+200)/2=400s; 600 > 400*1.5=600 → False (tie, not strictly greater)
    # Actually 600 > 400*1.5=600 is False. Let's check is_outlier logic: >, not >=
    assert s1["cohort_route_count"] == 2
    assert s1["cohort_avg_delay_sec"] == pytest.approx(400, abs=5)


@pytest.mark.asyncio
async def test_stop_profile_single_route_stop_never_outlier(stop_profile_client):
    client, aid = stop_profile_client
    r = await client.get(f"/api/{aid}/today/route/K31/stop-profile")
    stops = r.json()["stops"]
    s2 = next(s for s in stops if s["stop_sequence"] == 2)
    # S2 only has K31 in cohort → cohort_route_count=1 → is_outlier must be False
    assert s2["cohort_route_count"] == 1
    assert s2["is_outlier"] is False
