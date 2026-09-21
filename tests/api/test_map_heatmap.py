"""Tests for GET /api/{agency_id}/delays/heatmap — p90_delay_min field."""

from datetime import date, timedelta

import httpx
import pytest
from httpx import ASGITransport

from tests.conftest import _test_pool


@pytest.fixture
async def hmap_client(apply_schema):
    from api.main import app

    pool = await _test_pool()
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
        aid,
        "S1",
        "駅前",
        40.7,
        140.7,
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
            aid,
            "S1",
            today - timedelta(days=d_offset),
            "平日",
            "朝",
            ds,
            s,
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
    # Fixture has 3 total samples, well under LOW_CONFIDENCE_SAMPLES (30).
    assert props["samples"] == 3
    assert props["low_confidence"] is True


@pytest.mark.asyncio
async def test_heatmap_p90_null_when_no_data(hmap_client):
    client, aid = hmap_client
    # A historical range with no data. Not a FUTURE range: both range
    # boundaries are clamped to jst_today() (api.range.clamp_range_ctx), so a
    # far-future window collapses onto today — which this fixture does seed.
    r = await client.get(f"/api/{aid}/delays/heatmap?from=2020-01-01&to=2020-01-07")
    assert r.status_code == 200
    # No features expected (no data in that range)
    assert r.json()["features"] == []


@pytest.fixture
async def zero_sample_hmap_client(apply_schema):
    """One clustered stop whose only aggregate row records zero samples.

    `agg_stop_daily.samples` is NOT NULL but unconstrained above zero, so a
    cluster can legitimately sum to zero observations. The heatmap must report
    "no average" for it rather than dividing by zero.
    """
    from api.main import app

    pool = await _test_pool()
    app.state.pool = pool
    row = await pool.fetchrow(
        "INSERT INTO agencies (agency_name, feed_url) VALUES ($1, $2) RETURNING agency_id",
        "HmapZeroAgency",
        "http://hmap-zero-test.example.com",
    )
    aid = row["agency_id"]
    await pool.execute(
        "INSERT INTO static_stops (agency_id, stop_id, stop_name, stop_lat, stop_lon, geom) "
        "VALUES ($1, $2, $3, $4, $5, ST_SetSRID(ST_MakePoint($5,$4),4326))",
        aid,
        "S0",
        "無観測停留所",
        40.8,
        140.8,
    )
    await pool.execute(
        "INSERT INTO agg_stop_daily "
        "(agency_id, stop_id, date, service_type, time_band, delay_sum, samples) "
        "VALUES ($1,$2,$3,$4,$5,0,0)",
        aid,
        "S0",
        date.today(),
        "平日",
        "朝",
    )
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c, aid
    async with pool.acquire() as conn:
        await conn.execute("TRUNCATE agencies CASCADE")
    await pool.close()


@pytest.mark.asyncio
async def test_heatmap_zero_sample_cluster_reports_no_average(zero_sample_hmap_client):
    client, aid = zero_sample_hmap_client
    r = await client.get(f"/api/{aid}/delays/heatmap")
    assert r.status_code == 200
    features = r.json()["features"]
    assert len(features) == 1
    props = features[0]["properties"]
    assert props["avg_delay_min"] is None
    assert props["p90_delay_min"] is None
    assert props["samples"] == 0
