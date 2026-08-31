"""API tests for GET /api/{agency_id}/forecast/heatmap."""

import os

import asyncpg
import httpx
import pytest
from httpx import ASGITransport

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://localhost/transit")


@pytest.fixture
async def heatmap_client(apply_schema):
    from api.main import app

    pool = await asyncpg.create_pool(DATABASE_URL)
    app.state.pool = pool
    row = await pool.fetchrow(
        "INSERT INTO agencies (agency_name, feed_url) VALUES ($1, $2) RETURNING agency_id",
        "Heatmap Test Agency",
        "http://heatmap-test.example.com",
    )
    aid = row["agency_id"]
    await pool.executemany(
        "INSERT INTO agg_route_hour_dow "
        "(agency_id, route_code, service_type, dow, hour, avg_min, samples, sum_delay_sec) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8)",
        [
            # (dow1, h8) spans two service types -> pooled (4*100 + 6*100)/200 = 5.0
            # (sum_delay_sec here is an exact multiple of avg_min*60*samples,
            # so the exact-sum pooling agrees with a plain avg_min*samples
            # reweighting -- this row alone can't tell the two apart).
            (aid, "R1", "平日", 1, 8, 4.0, 100, int(4.0 * 60 * 100)),
            (aid, "R1", "祝日", 1, 8, 6.0, 100, int(6.0 * 60 * 100)),
            (aid, "R1", "土日祝", 6, 9, 2.0, 50, int(2.0 * 60 * 50)),
            (aid, "R2", "平日", 1, 8, 99.0, 999, int(99.0 * 60 * 999)),  # other route, must not leak
            # (dow3, h10): avg_min is deliberately NOT sum_delay_sec/60/samples
            # (as a real already-rounded per-bucket avg_min can diverge from
            # the exact raw-seconds mean) -- proves pooling reads
            # sum_delay_sec, not avg_min*samples. Exact: (290+100000)/60/1003
            # ~= 1.6667 -> rounds to 1.7; the old avg_min*samples reweighting
            # would instead give (1.61*3 + 2.0*1000)/1003 ~= 1.9988 -> 2.0.
            (aid, "R1", "A", 3, 10, 1.61, 3, 290),
            (aid, "R1", "B", 3, 10, 2.0, 1000, 100000),
        ],
    )
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, aid
    async with pool.acquire() as conn:
        await conn.execute("TRUNCATE agencies, agg_route_hour_dow CASCADE")
    await pool.close()


async def test_heatmap_pools_and_grids(heatmap_client):
    client, aid = heatmap_client
    r = await client.get(f"/api/{aid}/forecast/heatmap", params={"route": "R1"})
    assert r.status_code == 200
    body = r.json()
    by = {(c["dow"], c["hour"]): c for c in body["cells"]}
    assert len(body["cells"]) == 168
    assert by[(1, 8)]["expected_avg_min"] == 5.0  # pooled, not the R2 99
    assert by[(1, 8)]["samples"] == 200
    assert by[(6, 9)]["expected_avg_min"] == 2.0
    assert by[(2, 0)]["expected_avg_min"] is None and by[(2, 0)]["samples"] == 0
    assert body["route"] == "R1"
    assert body["disclaimer"]


async def test_heatmap_pools_exact_sum_delay_sec_not_reweighted_avg(heatmap_client):
    """(dow3, h10)'s two rows have an avg_min that deliberately does not match
    sum_delay_sec/60/samples -- the exact pooled mean (SUM(sum_delay_sec) /
    SUM(samples)) must win over the old, biased SUM(avg_min * samples) /
    SUM(samples) reweighting of an already-rounded per-row average."""
    client, aid = heatmap_client
    r = await client.get(f"/api/{aid}/forecast/heatmap", params={"route": "R1"})
    assert r.status_code == 200
    by = {(c["dow"], c["hour"]): c for c in r.json()["cells"]}
    cell = by[(3, 10)]
    assert cell["samples"] == 1003
    # Exact: (290 + 100000) / 60 / 1003 ~= 1.6667 -> 1.7, NOT the reweighted
    # (1.61*3 + 2.0*1000) / 1003 ~= 1.9988 -> 2.0.
    assert cell["expected_avg_min"] == 1.7


async def test_heatmap_requires_route(heatmap_client):
    client, aid = heatmap_client
    r = await client.get(f"/api/{aid}/forecast/heatmap")
    assert r.status_code == 422
