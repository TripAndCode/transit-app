"""Tests for GET /api/{agency_id}/delays/heatmap — p90_delay_min field."""
import os
from datetime import date, timedelta

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
