"""API tests for GET /api/{agency_id}/forecast."""

import os
from datetime import time

import asyncpg
import httpx
import pytest
from httpx import ASGITransport

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://localhost/transit")


@pytest.fixture
async def forecast_client(apply_schema):
    from api.main import app

    pool = await asyncpg.create_pool(DATABASE_URL)
    app.state.pool = pool
    row = await pool.fetchrow(
        "INSERT INTO agencies (agency_name, feed_url) VALUES ($1, $2) RETURNING agency_id",
        "Forecast Test Agency",
        "http://forecast-test.example.com",
    )
    agency_id = row["agency_id"]
    await pool.executemany(
        "INSERT INTO agg_route_hour (agency_id, route_code, service_type, scheduled_time, "
        "avg_min, p50_min, p90_min, samples) VALUES ($1,$2,$3,$4,$5,$6,$7,$8)",
        [
            (agency_id, "44372", "平日", time(17, 5), 6.0, 5.0, 10.0, 100),
            (agency_id, "44372", "平日", time(17, 40), 9.0, 8.0, 20.0, 300),
            (agency_id, "44372", "平日", time(8, 10), 2.0, 2.0, 4.0, 500),
        ],
    )
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, agency_id
    async with pool.acquire() as conn:
        await conn.execute("TRUNCATE agencies, agg_route_hour CASCADE")
    await pool.close()


async def test_forecast_weighted_for_hour(forecast_client):
    client, aid = forecast_client
    r = await client.get(f"/api/{aid}/forecast", params={"route": "44372", "service_type": "平日", "hour": 17})
    assert r.status_code == 200
    body = r.json()
    assert body["samples"] == 400
    assert body["expected_avg_min"] == round((6.0 * 100 + 9.0 * 300) / 400, 1)
    assert body["expected_p90_min"] == round((10.0 * 100 + 20.0 * 300) / 400, 1)
    assert body["low_confidence"] is False
    assert body["disclaimer"]


async def test_forecast_empty_hour_is_no_data(forecast_client):
    client, aid = forecast_client
    r = await client.get(f"/api/{aid}/forecast", params={"route": "44372", "service_type": "平日", "hour": 3})
    assert r.status_code == 200
    body = r.json()
    assert body["samples"] == 0
    assert body["expected_avg_min"] is None
    assert body["disclaimer"]


async def test_forecast_bad_hour_422(forecast_client):
    client, aid = forecast_client
    r = await client.get(f"/api/{aid}/forecast", params={"route": "44372", "service_type": "平日", "hour": 99})
    assert r.status_code == 422


async def test_forecast_missing_param_422(forecast_client):
    client, aid = forecast_client
    r = await client.get(f"/api/{aid}/forecast", params={"route": "44372", "hour": 17})
    assert r.status_code == 422
