"""API tests for GET /api/{agency_id}/forecast/profile."""

import os
from datetime import time

import asyncpg
import httpx
import pytest
from httpx import ASGITransport

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://localhost/transit")


@pytest.fixture
async def profile_client(apply_schema):
    from api.main import app

    pool = await asyncpg.create_pool(DATABASE_URL)
    app.state.pool = pool
    row = await pool.fetchrow(
        "INSERT INTO agencies (agency_name, feed_url) VALUES ($1, $2) RETURNING agency_id",
        "Profile Test Agency",
        "http://profile-test.example.com",
    )
    agency_id = row["agency_id"]
    await pool.executemany(
        "INSERT INTO agg_route_hour (agency_id, route_code, service_type, scheduled_time, "
        "avg_min, p50_min, p90_min, samples) VALUES ($1,$2,$3,$4,$5,$6,$7,$8)",
        [
            # hour 8 has two buckets -> pooled (4*100 + 6*100)/200 = 5.0
            (agency_id, "R1", "平日", time(8, 5), 4.0, 4.0, 8.0, 100),
            (agency_id, "R1", "平日", time(8, 20), 6.0, 6.0, 12.0, 100),
            # hour 9: single bucket, low-confidence (10 samples)
            (agency_id, "R1", "平日", time(9, 10), 2.0, 2.0, 4.0, 10),
            # different service type — must not bleed in
            (agency_id, "R1", "土日祝", time(8, 5), 99.0, 99.0, 99.0, 999),
        ],
    )
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, agency_id
    async with pool.acquire() as conn:
        await conn.execute("TRUNCATE agencies, agg_route_hour CASCADE")
    await pool.close()


async def test_profile_pools_and_grids(profile_client):
    client, aid = profile_client
    r = await client.get(
        f"/api/{aid}/forecast/profile", params={"route": "R1", "service_type": "平日"}
    )
    assert r.status_code == 200
    body = r.json()
    hours = {h["hour"]: h for h in body["hours"]}
    assert len(body["hours"]) == 24
    assert hours[8]["expected_avg_min"] == 5.0  # pooled, not the 土日祝 99
    assert hours[8]["samples"] == 200
    assert hours[8]["low_confidence"] is False
    assert hours[9]["expected_avg_min"] == 2.0
    assert hours[9]["low_confidence"] is True  # 10 < 30
    assert hours[10]["expected_avg_min"] is None
    assert hours[10]["samples"] == 0
    assert body["disclaimer"]
    assert body["route"] == "R1"
    assert body["service_type"] == "平日"


async def test_profile_unknown_route_all_null(profile_client):
    client, aid = profile_client
    r = await client.get(
        f"/api/{aid}/forecast/profile", params={"route": "NOPE", "service_type": "平日"}
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body["hours"]) == 24
    assert all(h["expected_avg_min"] is None and h["samples"] == 0 for h in body["hours"])
    assert body["disclaimer"]


async def test_profile_missing_param_422(profile_client):
    client, aid = profile_client
    r = await client.get(f"/api/{aid}/forecast/profile", params={"route": "R1"})
    assert r.status_code == 422


async def test_services_lists_distinct_sorted(profile_client):
    client, aid = profile_client
    r = await client.get(f"/api/{aid}/forecast/services")
    assert r.status_code == 200
    # fixture seeds both 平日 and 土日祝 for this agency (order is collation-dependent)
    assert set(r.json()["service_types"]) == {"平日", "土日祝"}
