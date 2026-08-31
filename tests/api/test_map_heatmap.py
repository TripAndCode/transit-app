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
    # Request a future date range with no data
    r = await client.get(f"/api/{aid}/delays/heatmap?from=2099-01-01&to=2099-01-07")
    assert r.status_code == 200
    # No features expected (no data in that range)
    assert r.json()["features"] == []


@pytest.fixture
async def stop_profile_client(apply_schema, ch_client, ch_async_client):
    from api.main import app

    pool = await asyncpg.create_pool(DATABASE_URL)
    app.state.pool = pool
    app.state.ch_client = ch_async_client
    row = await pool.fetchrow(
        "INSERT INTO agencies (agency_name, feed_url) VALUES ($1, $2) RETURNING agency_id",
        "StopCohortAgency",
        "http://stop-cohort-test.example.com",
    )
    aid = row["agency_id"]

    # Two static stops
    for stop_id, name, lat, lon in [("S1", "駅前", 40.7, 140.7), ("S2", "市役所", 40.8, 140.8)]:
        await pool.execute(
            "INSERT INTO static_stops (agency_id, stop_id, stop_name, stop_lat, stop_lon, geom) "
            "VALUES ($1,$2,$3,$4,$5,ST_SetSRID(ST_MakePoint($5,$4),4326))",
            aid,
            stop_id,
            name,
            lat,
            lon,
        )
    # Route K31 trips: stop_seq 1→S1, 2→S2
    await pool.execute(
        "INSERT INTO static_trips (agency_id, trip_id, route_id, service_id) VALUES ($1,$2,$3,$4)",
        aid,
        "T1",
        "K31",
        "WD",
    )
    for seq, stop_id in [(1, "S1"), (2, "S2")]:
        await pool.execute(
            "INSERT INTO static_stop_times (agency_id, trip_id, stop_sequence, stop_id) VALUES ($1,$2,$3,$4)",
            aid,
            "T1",
            seq,
            stop_id,
        )
    # agg_route_daily existence row: route_stop_profile prechecks this table
    # (agency_id, route_code) before touching ClickHouse at all — checks
    # agg_route_daily, not agg_route_stats, since the latter is a lossy
    # existence oracle (built with a NOT NULL service_type filter — see
    # map.py's route_trips docstring). Real avg/worst/trips/samples figures
    # don't matter here, only that a row exists.
    await pool.execute(
        "INSERT INTO agg_route_daily (agency_id, date, route_code, service_type, "
        "avg_delay_sec, worst_delay_sec, trips_observed, samples, last_seen_at, sum_delay_sec) "
        "VALUES ($1, CURRENT_DATE, 'K31', '平日', 0, 0, 1, 1, NOW(), 0)",
        aid,
    )
    # Raw updates for today (K31 with big delay at S1)
    today = date.today()
    jst = timezone(timedelta(hours=9))
    ts = datetime(today.year, today.month, today.day, 9, 0, 0, tzinfo=jst)
    await pool.execute(
        "INSERT INTO updates (agency_id, file_name, captured_at, trip_id, service_type, "
        "scheduled_time, route_code, stop_sequence, dep_delay) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)",
        aid,
        "f1.pb",
        ts,
        "T1",
        "平日",
        time(9, 0),
        "K31",
        1,
        600,
    )
    await pool.execute(
        "INSERT INTO updates (agency_id, file_name, captured_at, trip_id, service_type, "
        "scheduled_time, route_code, stop_sequence, dep_delay) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)",
        aid,
        "f1.pb",
        ts,
        "T1",
        "平日",
        time(9, 0),
        "K31",
        2,
        60,
    )
    # agg_route_stop_daily: S1 has three routes — K31 (600s avg), K99 (200s avg), K98 (100s avg)
    # cohort_avg = (600+200+100)/3 = 300s; K31 avg 600 > 300*1.5=450 → is_outlier=True for K31
    for route_code, ds, s in [("K31", 600, 1), ("K99", 200, 1), ("K98", 100, 1)]:
        await pool.execute(
            "INSERT INTO agg_route_stop_daily "
            "(agency_id, route_code, stop_id, date, service_type, time_band, delay_sum, samples) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8) ON CONFLICT DO NOTHING",
            aid,
            route_code,
            "S1",
            today,
            "平日",
            "朝",
            ds,
            s,
        )
    # S2 only has K31 — cohort_route_count=1, so never outlier
    await pool.execute(
        "INSERT INTO agg_route_stop_daily "
        "(agency_id, route_code, stop_id, date, service_type, time_band, delay_sum, samples) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8) ON CONFLICT DO NOTHING",
        aid,
        "K31",
        "S2",
        today,
        "平日",
        "朝",
        60,
        1,
    )
    from tests.conftest import mirror_updates_to_ch

    mirror_updates_to_ch(ch_client, aid)

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
    # S1: K31 avg=600s, cohort avg=(600+200+100)/3=300s; 600 > 300*1.5=450 → True
    assert s1["cohort_route_count"] == 3
    assert s1["cohort_avg_delay_sec"] == pytest.approx(300, abs=5)
    # Fixture's 3 cohort rows are samples=1 each -> cohort_samples=3, well
    # under COHORT_LOW_CONFIDENCE_SAMPLES (10).
    assert s1["cohort_samples"] == 3
    assert s1["cohort_low_confidence"] is True


@pytest.mark.asyncio
async def test_stop_profile_single_route_stop_never_outlier(stop_profile_client):
    client, aid = stop_profile_client
    r = await client.get(f"/api/{aid}/today/route/K31/stop-profile")
    stops = r.json()["stops"]
    s2 = next(s for s in stops if s["stop_sequence"] == 2)
    # S2 only has K31 in cohort → cohort_route_count=1 → is_outlier must be False
    assert s2["cohort_route_count"] == 1
    assert s2["is_outlier"] is False


@pytest.mark.asyncio
async def test_stop_profile_outlier_true(stop_profile_client):
    client, aid = stop_profile_client
    r = await client.get(f"/api/{aid}/today/route/K31/stop-profile")
    assert r.status_code == 200
    stops = r.json()["stops"]
    s1 = next(s for s in stops if s["stop_sequence"] == 1)
    # K31 avg=600s; cohort includes K31+K99+K98, avg=(600+200+100)/3=300s; 600 > 300*1.5=450 → True
    assert s1["is_outlier"] is True
    assert s1["cohort_route_count"] == 3


@pytest.fixture
async def weighted_cohort_client(apply_schema, ch_client, ch_async_client):
    """Fixture whose cohort rows have deliberately unequal `samples` so a
    naive per-row AVG (the pre-fix behavior) and a samples-weighted average
    diverge. `stop_profile_client` above can't distinguish the two: every
    one of its agg_route_stop_daily rows has samples=1, so a plain AVG of
    each row's ratio happens to equal the samples-weighted average."""
    from api.main import app

    pool = await asyncpg.create_pool(DATABASE_URL)
    app.state.pool = pool
    app.state.ch_client = ch_async_client
    row = await pool.fetchrow(
        "INSERT INTO agencies (agency_name, feed_url) VALUES ($1, $2) RETURNING agency_id",
        "WeightedCohortAgency",
        "http://weighted-cohort-test.example.com",
    )
    aid = row["agency_id"]

    await pool.execute(
        "INSERT INTO static_stops (agency_id, stop_id, stop_name, stop_lat, stop_lon, geom) "
        "VALUES ($1,'SW1','重み駅',40.7,140.7,ST_SetSRID(ST_MakePoint(140.7,40.7),4326))",
        aid,
    )
    await pool.execute(
        "INSERT INTO static_trips (agency_id, trip_id, route_id, service_id) VALUES ($1,'T1','R_W','WD')",
        aid,
    )
    await pool.execute(
        "INSERT INTO static_stop_times (agency_id, trip_id, stop_sequence, stop_id) VALUES ($1,'T1',1,'SW1')",
        aid,
    )
    # agg_route_daily existence row: route_stop_profile prechecks this table
    # before touching ClickHouse at all (see stop_profile_client above).
    await pool.execute(
        "INSERT INTO agg_route_daily (agency_id, date, route_code, service_type, "
        "avg_delay_sec, worst_delay_sec, trips_observed, samples, last_seen_at, sum_delay_sec) "
        "VALUES ($1, CURRENT_DATE, 'R_W', '平日', 0, 0, 1, 1, NOW(), 0)",
        aid,
    )
    today = date.today()
    jst = timezone(timedelta(hours=9))
    ts = datetime(today.year, today.month, today.day, 9, 0, 0, tzinfo=jst)
    await pool.execute(
        "INSERT INTO updates (agency_id, file_name, captured_at, trip_id, service_type, "
        "scheduled_time, route_code, stop_sequence, dep_delay) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)",
        aid,
        "f1.pb",
        ts,
        "T1",
        "平日",
        time(9, 0),
        "R_W",
        1,
        60,
    )
    # Cohort rows for SW1: a low-sample row (ratio 200/2=100s) and a
    # high-sample row (ratio 25000/500=50s). Naive per-row AVG of the two
    # ratios is (100+50)/2=75; the samples-weighted average is
    # (200+25000)/(2+500)=50.2 -> rounds to 50. The two must disagree, or
    # this test can't catch a regression back to the unweighted form.
    for route_code, ds, s in [("R_W", 200, 2), ("R_OTHER", 25000, 500)]:
        await pool.execute(
            "INSERT INTO agg_route_stop_daily "
            "(agency_id, route_code, stop_id, date, service_type, time_band, delay_sum, samples) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8) ON CONFLICT DO NOTHING",
            aid,
            route_code,
            "SW1",
            today,
            "平日",
            "朝",
            ds,
            s,
        )
    from tests.conftest import mirror_updates_to_ch

    mirror_updates_to_ch(ch_client, aid)

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c, aid
    async with pool.acquire() as conn:
        await conn.execute("TRUNCATE agencies CASCADE")
    await pool.close()


@pytest.mark.asyncio
async def test_stop_profile_cohort_avg_is_samples_weighted(weighted_cohort_client):
    """cohort_avg_delay_sec must weight each cohort row by its own `samples`,
    matching every other averaging site in map.py (today_route_summary's
    `rb` CTE at map.py:635-636, delay_heatmap's `avg_delay_min` at
    map.py:1062) rather than a plain AVG over each row's own
    delay_sum/samples ratio, where a 2-sample row would otherwise count the
    same as a 500-sample row."""
    client, aid = weighted_cohort_client
    r = await client.get(f"/api/{aid}/today/route/R_W/stop-profile")
    assert r.status_code == 200
    stops = r.json()["stops"]
    s1 = next(s for s in stops if s["stop_sequence"] == 1)
    assert s1["cohort_route_count"] == 2
    # Samples-weighted: (200+25000)/(2+500) ≈ 50.2 -> 50. A naive per-row
    # average of the two ratios (100s and 50s) would instead give 75.
    assert s1["cohort_avg_delay_sec"] == 50
    # 502 total cohort samples clears COHORT_LOW_CONFIDENCE_SAMPLES (10),
    # unlike stop_profile_client's thinner (3-sample) cohort above.
    assert s1["cohort_samples"] == 502
    assert s1["cohort_low_confidence"] is False
