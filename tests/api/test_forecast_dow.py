"""API tests for GET /api/{agency_id}/forecast/dow."""

import os

import asyncpg
import httpx
import pytest
from httpx import ASGITransport

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://localhost/transit")


@pytest.fixture
async def dow_client(apply_schema):
    from api.main import app

    pool = await asyncpg.create_pool(DATABASE_URL)
    app.state.pool = pool
    row = await pool.fetchrow(
        "INSERT INTO agencies (agency_name, feed_url) VALUES ($1,$2) RETURNING agency_id",
        "DOW Test Agency",
        "http://dow-test.example.com",
    )
    aid = row["agency_id"]
    await pool.executemany(
        "INSERT INTO agg_route_dow (agency_id, route_code, service_type, dow, avg_min, samples) "
        "VALUES ($1,$2,$3,$4,$5,$6)",
        [
            # dow 1 spans two service types -> pooled (4*100 + 6*100)/200 = 5.0
            (aid, "R1", "平日", 1, 4.0, 100),
            (aid, "R1", "祝日", 1, 6.0, 100),
            (aid, "R1", "土日祝", 6, 2.0, 50),
            (aid, "R2", "平日", 1, 99.0, 999),  # different route, must not leak
        ],
    )
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, aid
    async with pool.acquire() as conn:
        await conn.execute("TRUNCATE agencies, agg_route_dow CASCADE")
    await pool.close()


async def test_dow_pools_across_service_and_grids(dow_client):
    client, aid = dow_client
    r = await client.get(f"/api/{aid}/forecast/dow", params={"route": "R1"})
    assert r.status_code == 200
    body = r.json()
    days = {d["dow"]: d for d in body["days"]}
    assert len(body["days"]) == 7
    assert days[1]["expected_avg_min"] == 5.0  # pooled across 平日+祝日, not the R2 99
    assert days[1]["samples"] == 200
    assert days[6]["expected_avg_min"] == 2.0
    assert days[2]["expected_avg_min"] is None and days[2]["samples"] == 0
    assert body["route"] == "R1"
    assert body["disclaimer"]


async def test_dow_missing_param_422(dow_client):
    client, aid = dow_client
    r = await client.get(f"/api/{aid}/forecast/dow")
    assert r.status_code == 422
