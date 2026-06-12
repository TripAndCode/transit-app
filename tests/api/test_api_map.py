import os

import asyncpg
import httpx
import pytest
from httpx import ASGITransport

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://localhost/transit")


@pytest.fixture
async def map_app(apply_schema):
    from api.main import app

    pool = await asyncpg.create_pool(DATABASE_URL)
    app.state.pool = pool
    row = await pool.fetchrow(
        "INSERT INTO agencies (agency_name, feed_url) VALUES ($1, $2) RETURNING agency_id",
        "Map Test Agency",
        "http://map-test.example.com",
    )
    agency_id = row["agency_id"]
    yield app, agency_id
    async with pool.acquire() as conn:
        await conn.execute(
            "TRUNCATE agencies, updates, static_stops, static_stop_times, "
            "static_trips, static_routes, static_calendar_dates, static_shapes, "
            "agg_route_stats, agg_route_hour, agg_route_dow, "
            "agg_daily_trend, agg_stop_seq, agg_stop_daily, agg_stop_routes, "
            "rag_chunks CASCADE"
        )
    await pool.close()


@pytest.fixture
async def map_client(map_app):
    app, agency_id = map_app
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, agency_id


@pytest.mark.asyncio
async def test_live_delays_empty(map_client):
    client, agency_id = map_client
    resp = await client.get(f"/api/{agency_id}/delays/live")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload == {"latest_captured_at": None, "rows": []}


@pytest.mark.asyncio
async def test_live_delays_unknown_agency(map_client):
    client, _ = map_client
    resp = await client.get("/api/99999/delays/live")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_heatmap_empty(map_client):
    client, agency_id = map_client
    resp = await client.get(f"/api/{agency_id}/delays/heatmap")
    assert resp.status_code == 200
    data = resp.json()
    assert data["type"] == "FeatureCollection"
    assert "features" in data
    assert isinstance(data["features"], list)


@pytest.mark.asyncio
async def test_routes_list_empty(map_client):
    client, agency_id = map_client
    resp = await client.get(f"/api/{agency_id}/routes")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_stops_list_empty(map_client):
    client, agency_id = map_client
    resp = await client.get(f"/api/{agency_id}/stops")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_route_shape_returns_geometry_when_shapes_loaded(map_app):
    """When static_trips.shape_id resolves to a static_shapes row, the
    endpoint returns a GeoJSON LineString."""
    app, agency_id = map_app
    pool = app.state.pool
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO static_trips (agency_id, trip_id, route_id, shape_id) "
            "VALUES ($1, 'T1', 'R1', 'S1'), ($1, 'T2', 'R1', 'S1')",
            agency_id,
        )
        # Static stops so the dedup CTE doesn't return zero rows
        await conn.execute(
            "INSERT INTO static_stops (agency_id, stop_id, stop_name, stop_lat, stop_lon, geom) "
            "VALUES ($1, 'ST1', '駅前', 40.82, 140.74, ST_SetSRID(ST_MakePoint(140.74, 40.82), 4326)), "
            "       ($1, 'ST2', '次の停留所', 40.83, 140.75, ST_SetSRID(ST_MakePoint(140.75, 40.83), 4326))",
            agency_id,
        )
        await conn.execute(
            "INSERT INTO static_stop_times (agency_id, trip_id, stop_sequence, stop_id, arrival_time, departure_time) "
            "VALUES ($1, 'T1', 1, 'ST1', '09:00:00', '09:00:00'), "
            "       ($1, 'T1', 2, 'ST2', '09:05:00', '09:05:00')",
            agency_id,
        )
        # Updates: two rows linking trip_id back to a route_code so the dedup CTE returns rows
        await conn.execute(
            "INSERT INTO updates (agency_id, trip_id, route_code, stop_sequence, dep_delay, captured_at, "
            "file_name, service_type, scheduled_time) "
            "VALUES ($1, 'T1', 'R1', 1, 60, NOW(), 'test.pb', 'weekday', '09:00:00'), "
            "       ($1, 'T1', 'R1', 2, 90, NOW(), 'test.pb', 'weekday', '09:05:00')",
            agency_id,
        )
        await conn.execute(
            "INSERT INTO static_shapes (agency_id, shape_id, geom) "
            "VALUES ($1, 'S1', "
            "ST_SetSRID(ST_MakeLine(ARRAY[ST_MakePoint(140.74, 40.82), ST_MakePoint(140.75, 40.83)]), 4326))",
            agency_id,
        )

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/{agency_id}/route-shape?route=R1")

    assert resp.status_code == 200
    body = resp.json()
    assert body["route"] == "R1"
    assert body["geometry"] is not None, body
    assert body["geometry"]["type"] == "LineString"
    assert isinstance(body["geometry"]["coordinates"], list)
    assert len(body["geometry"]["coordinates"]) >= 2
    lon, lat = body["geometry"]["coordinates"][0]
    assert 139.0 < lon < 142.0
    assert 39.0 < lat < 42.0


async def _seed_route(pool, agency_id, route_code, service_type, day_rows, baseline=None):
    """day_rows: list of (trip_id, stop_sequence, dep_delay_sec, scheduled_time).
    baseline: optional (avg_min, p90_min, samples) -> inserted into agg_route_stats."""
    from datetime import datetime, time, timezone

    async with pool.acquire() as conn:
        for i, (trip_id, seq, delay, sched) in enumerate(day_rows):
            # scheduled_time column is TIME WITHOUT TIME ZONE; asyncpg needs a
            # datetime.time object, not a string.
            if isinstance(sched, str):
                parts = sched.split(":")
                sched = time(int(parts[0]), int(parts[1]), int(parts[2]) if len(parts) > 2 else 0)
            await conn.execute(
                "INSERT INTO updates (agency_id, file_name, captured_at, trip_id, "
                "service_type, scheduled_time, route_code, stop_sequence, dep_delay) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)",
                agency_id,
                f"f{i}.pb",
                datetime(2026, 6, 9, 10, 0, 0, tzinfo=timezone.utc),
                trip_id,
                service_type,
                sched,
                route_code,
                seq,
                delay,
            )
        if baseline is not None:
            avg_min, p90_min, samples = baseline
            await conn.execute(
                "INSERT INTO agg_route_stats (agency_id, route_code, service_type, "
                "avg_min, p90_min, samples) VALUES ($1,$2,$3,$4,$5,$6)",
                agency_id,
                route_code,
                service_type,
                avg_min,
                p90_min,
                samples,
            )


@pytest.mark.asyncio
async def test_route_summary_buckets_and_deviation(map_app):
    app, agency_id = map_app
    pool = app.state.pool
    # Anomaly: today avg 420s, baseline avg 120s (2min) p90 360s (6min), 40 samples
    await _seed_route(
        pool,
        agency_id,
        "R_ANOM",
        "平日",
        [(f"t{i}", 1, 420, "10:00") for i in range(40)],
        baseline=(2.0, 6.0, 500),
    )
    # No baseline route
    await _seed_route(
        pool,
        agency_id,
        "R_NOBASE",
        "平日",
        [(f"n{i}", 1, 300, "11:00") for i in range(40)],
        baseline=None,
    )
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/{agency_id}/today/route-summary")
    assert resp.status_code == 200
    routes = {r["route_code"]: r for r in resp.json()["routes"]}

    anom = routes["R_ANOM"]
    assert anom["bucket"] == "anomaly"
    assert anom["has_baseline"] is True
    assert anom["baseline_avg_sec"] == 120
    assert anom["baseline_p90_sec"] == 360
    assert anom["deviation_sec"] == 300  # 420 - 120
    assert anom["low_confidence"] is False

    nobase = routes["R_NOBASE"]
    assert nobase["bucket"] == "no_baseline"
    assert nobase["has_baseline"] is False
    assert nobase["baseline_avg_sec"] is None
    assert nobase["deviation_sec"] is None


@pytest.mark.asyncio
async def test_route_summary_low_confidence_caps_anomaly(map_app):
    app, agency_id = map_app
    pool = app.state.pool
    # Would be anomaly (avg 420 > p90 360) but only 5 obs -> watch + low_confidence
    await _seed_route(
        pool,
        agency_id,
        "R_THIN",
        "平日",
        [(f"thin{i}", 1, 420, "10:00") for i in range(5)],
        baseline=(2.0, 6.0, 500),
    )
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/{agency_id}/today/route-summary")
    r = next(x for x in resp.json()["routes"] if x["route_code"] == "R_THIN")
    assert r["bucket"] == "watch"
    assert r["low_confidence"] is True


@pytest.mark.asyncio
async def test_route_trips_drilldown(map_app):
    app, agency_id = map_app
    pool = app.state.pool
    # trip A: two stops, delays 600 & 540 -> avg 570; trip B: one stop, 120
    await _seed_route(
        pool,
        agency_id,
        "R_DRILL",
        "平日",
        [("A", 1, 600, "08:40"), ("A", 2, 540, "08:40"), ("B", 1, 120, "12:05")],
    )
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO static_trips (agency_id, trip_id, trip_headsign) VALUES ($1,'A','造道行'),($1,'B','八重田行')",
            agency_id,
        )
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/{agency_id}/today/route/R_DRILL/trips")
    assert resp.status_code == 200
    trips = resp.json()["trips"]
    assert [t["trip_id"] for t in trips] == ["A", "B"]  # worst first
    assert trips[0]["avg_delay_sec"] == 570
    assert trips[0]["headsign"] == "造道行"
    assert trips[0]["scheduled_time"] == "08:40"
    assert trips[1]["avg_delay_sec"] == 120


@pytest.mark.asyncio
async def test_route_trips_empty_when_no_data(map_client):
    client, agency_id = map_client
    resp = await client.get(f"/api/{agency_id}/today/route/NOPE/trips")
    assert resp.status_code == 200
    assert resp.json() == {"date": None, "trips": []}


@pytest.mark.asyncio
async def test_route_stop_profile_drilldown(map_app):
    app, agency_id = map_app
    pool = app.state.pool
    # seq 1: delays 60 & 120 -> avg 90; seq 2: 600 -> avg 600 (bottleneck)
    await _seed_route(
        pool,
        agency_id,
        "R_PROF",
        "平日",
        [("A", 1, 60, "08:40"), ("B", 1, 120, "09:00"), ("A", 2, 600, "08:40")],
    )
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO static_stops (agency_id, stop_id, stop_name, geom) "
            "VALUES ($1,'s1','始発',ST_SetSRID(ST_MakePoint(140.7,40.8),4326)),"
            "       ($1,'s2','中央病院前',ST_SetSRID(ST_MakePoint(140.71,40.81),4326))",
            agency_id,
        )
        await conn.execute(
            "INSERT INTO static_stop_times (agency_id, trip_id, stop_sequence, stop_id) "
            "VALUES ($1,'A',1,'s1'),($1,'A',2,'s2'),($1,'B',1,'s1')",
            agency_id,
        )
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/{agency_id}/today/route/R_PROF/stop-profile")
    assert resp.status_code == 200
    stops = resp.json()["stops"]
    assert [s["stop_sequence"] for s in stops] == [1, 2]  # ordered by sequence
    assert stops[0]["stop_name"] == "始発"
    assert stops[0]["avg_delay_sec"] == 90
    assert stops[1]["stop_name"] == "中央病院前"
    assert stops[1]["avg_delay_sec"] == 600


@pytest.mark.asyncio
async def test_route_shape_returns_null_geometry_when_no_shapes_loaded(map_app):
    """If trips have a shape_id but static_shapes has no matching row,
    geometry is null and stops are still populated."""
    app, agency_id = map_app
    pool = app.state.pool
    async with pool.acquire() as conn:
        # Same setup as the positive test, but DO NOT insert into static_shapes
        await conn.execute(
            "INSERT INTO static_trips (agency_id, trip_id, route_id, shape_id) VALUES ($1, 'T1', 'R1', 'S1')",
            agency_id,
        )
        await conn.execute(
            "INSERT INTO static_stops (agency_id, stop_id, stop_name, stop_lat, stop_lon, geom) "
            "VALUES ($1, 'ST1', '駅前', 40.82, 140.74, ST_SetSRID(ST_MakePoint(140.74, 40.82), 4326)), "
            "       ($1, 'ST2', '次の停留所', 40.83, 140.75, ST_SetSRID(ST_MakePoint(140.75, 40.83), 4326))",
            agency_id,
        )
        await conn.execute(
            "INSERT INTO static_stop_times (agency_id, trip_id, stop_sequence, stop_id, arrival_time, departure_time) "
            "VALUES ($1, 'T1', 1, 'ST1', '09:00:00', '09:00:00'), "
            "       ($1, 'T1', 2, 'ST2', '09:05:00', '09:05:00')",
            agency_id,
        )
        await conn.execute(
            "INSERT INTO updates (agency_id, trip_id, route_code, stop_sequence, dep_delay, captured_at, "
            "file_name, service_type, scheduled_time) "
            "VALUES ($1, 'T1', 'R1', 1, 60, NOW(), 'test.pb', 'weekday', '09:00:00'), "
            "       ($1, 'T1', 'R1', 2, 90, NOW(), 'test.pb', 'weekday', '09:05:00')",
            agency_id,
        )

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/{agency_id}/route-shape?route=R1")

    assert resp.status_code == 200
    body = resp.json()
    assert body["geometry"] is None, body
    assert len(body["stops"]) >= 2


async def _seed_heatmap(pool, agency_id):
    from datetime import datetime, time, timezone

    async with pool.acquire() as c:
        await c.execute(
            "INSERT INTO static_stops (agency_id, stop_id, stop_name, geom) "
            "VALUES ($1,'s1','駅前',ST_SetSRID(ST_MakePoint(140.74,40.82),4326))",
            agency_id,
        )
        await c.execute(
            "INSERT INTO static_stop_times (agency_id, trip_id, stop_sequence, stop_id) "
            "VALUES ($1,'T',1,'s1')",
            agency_id,
        )
        for i, d in enumerate([60, 120, 180]):
            await c.execute(
                "INSERT INTO updates (agency_id, file_name, captured_at, trip_id, service_type, "
                "scheduled_time, route_code, stop_sequence, dep_delay) "
                "VALUES ($1,$2,$3,'T','平日',$4,'R1',1,$5)",
                agency_id,
                f"f{i}.pb",
                datetime(2026, 6, 9, 8, 10, tzinfo=timezone.utc),
                time(8, 10),
                d,
            )


def _run_analyze(agency_id):
    import os

    import psycopg2

    from pipeline.analyze import analyze

    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    try:
        analyze(agency_id, conn)
        conn.commit()
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_heatmap_agg_path_matches_raw(map_app):
    """No route filter -> aggregate path. avg=(60+120+180)/3=120s=2.0min, samples=3."""
    app, agency_id = map_app
    await _seed_heatmap(app.state.pool, agency_id)
    _run_analyze(agency_id)  # populate agg_stop_daily + agg_stop_routes
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/{agency_id}/delays/heatmap")
    assert resp.status_code == 200
    feats = resp.json()["features"]
    assert len(feats) == 1
    p = feats[0]["properties"]
    assert p["samples"] == 3
    assert abs(p["avg_delay_min"] - 2.0) < 1e-6
    assert "R1" in p["route_codes"]


@pytest.mark.asyncio
async def test_heatmap_route_filter_uses_live_path(map_app):
    """Route filter -> live path; works even though agg was never built."""
    app, agency_id = map_app
    await _seed_heatmap(app.state.pool, agency_id)  # NOTE: no _run_analyze -> agg empty
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/{agency_id}/delays/heatmap?routes=R1")
    assert resp.status_code == 200
    feats = resp.json()["features"]
    assert len(feats) == 1
    assert feats[0]["properties"]["samples"] == 3  # live counts raw rows
