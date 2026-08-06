import os

import asyncpg
import httpx
import pytest
from httpx import ASGITransport

from api.middleware.ratelimit import limiter

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://localhost/transit")


@pytest.fixture(autouse=True)
def _reset_limiter():
    limiter.reset()
    yield


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
            "agg_daily_trend, agg_route_daily, agg_stop_seq, agg_stop_daily, agg_stop_routes, "
            "agg_route_stop_daily, agg_feed_health, rag_chunks CASCADE"
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
async def test_live_delays_is_rate_limited_after_repeated_requests(map_client):
    """map.py's endpoints must be throttled like every other public read
    router (overview/network/reports/ask) — unauthenticated callers can
    otherwise hit the heaviest live-scan endpoints without limit. Doesn't
    assert the exact configured threshold, only that the protection exists."""
    client, agency_id = map_client
    statuses = []
    for _ in range(65):
        resp = await client.get(f"/api/{agency_id}/delays/live")
        statuses.append(resp.status_code)
        if resp.status_code == 429:
            break
    assert 429 in statuses, f"never throttled after 65 requests: {statuses}"


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
async def test_heatmap_route_filter_from_aggregate(map_app):
    """Route-filtered heatmap reads agg_route_stop_daily (not raw updates): one dot
    per stop with the route's avg delay; the route_codes label comes from the agg."""
    app, agency_id = map_app
    pool = app.state.pool
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO static_stops (agency_id, stop_id, stop_name, stop_lat, stop_lon, geom) "
            "VALUES ($1, 'S1', '駅前', 40.0, 140.0, ST_SetSRID(ST_MakePoint(140.0, 40.0), 4326))",
            agency_id,
        )
        # Two agg rows for R1/S1 on the same date/band: 60s/1 and 120s/1 -> avg 90s = 1.5min
        await conn.execute(
            "INSERT INTO agg_route_stop_daily "
            "(agency_id, route_code, stop_id, date, service_type, time_band, delay_sum, samples) "
            "VALUES ($1,'R1','S1','2026-06-06','weekday','morning',60,1), "
            "       ($1,'R1','S1','2026-06-06','','morning',120,1)",
            agency_id,
        )
        # A different route on the same stop must NOT leak into an R1-filtered request
        await conn.execute(
            "INSERT INTO agg_route_stop_daily "
            "(agency_id, route_code, stop_id, date, service_type, time_band, delay_sum, samples) "
            "VALUES ($1,'R2','S1','2026-06-06','weekday','morning',6000,1)",
            agency_id,
        )
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/{agency_id}/delays/heatmap?routes=R1&from=2026-06-06&to=2026-06-06")
    assert resp.status_code == 200
    feats = resp.json()["features"]
    assert len(feats) == 1
    props = feats[0]["properties"]
    assert props["avg_delay_min"] == 1.5  # (60+120)/2/60; R2 excluded
    assert props["samples"] == 2
    assert props["route_codes"] == "R1"


@pytest.mark.asyncio
async def test_heatmap_merges_same_name_stops_across_a_grid_boundary(map_app):
    """Two platforms of the same named stop, ~171m apart, straddling a
    ST_SnapToGrid(0.05) rounding boundary (139.974 rounds to the 139.95 grid
    point, 139.976 rounds to the 140.00 grid point — the midpoint boundary
    sits at 139.975) must still merge into ONE heatmap dot.

    Grid-snap clustering is axis-aligned and unshifted, so two points this
    close can still land on different grid points purely from boundary
    alignment — confirmed happening on real data (agencies with real GTFS
    feeds) for ~1.2% of same-named pairs within 200m. Distance-based
    clustering (ST_ClusterDBSCAN) doesn't have this failure mode."""
    app, agency_id = map_app
    pool = app.state.pool
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO static_stops (agency_id, stop_id, stop_name, stop_lat, stop_lon, geom) "
            "VALUES ($1, 'SA', '境界前', 40.0, 139.974, ST_SetSRID(ST_MakePoint(139.974, 40.0), 4326)), "
            "       ($1, 'SB', '境界前', 40.0, 139.976, ST_SetSRID(ST_MakePoint(139.976, 40.0), 4326))",
            agency_id,
        )
        await conn.execute(
            "INSERT INTO agg_stop_daily (agency_id, stop_id, date, service_type, time_band, delay_sum, samples) "
            "VALUES ($1,'SA','2026-06-06','weekday','morning',60,1), "
            "       ($1,'SB','2026-06-06','weekday','morning',180,1)",
            agency_id,
        )
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/{agency_id}/delays/heatmap?from=2026-06-06&to=2026-06-06")
    assert resp.status_code == 200
    feats = resp.json()["features"]
    assert len(feats) == 1, feats  # would be 2 under plain ST_SnapToGrid
    props = feats[0]["properties"]
    assert props["samples"] == 2
    assert props["avg_delay_min"] == 2.0  # (60+180)/2/60
    lon, _lat = feats[0]["geometry"]["coordinates"]
    assert 139.974 < lon < 139.976  # centroid of the two merged points


@pytest.mark.asyncio
async def test_heatmap_does_not_merge_same_name_stops_far_apart(map_app):
    """Two same-named stops ~841m apart (beyond `eps`) must stay two dots.

    DBSCAN with minpoints := 1 makes every point a core point, so clusters
    chain transitively — an `eps` reused from the old grid's ~5.5km cell
    SIZE (rather than sized as an actual merge radius) bridged genuinely
    distant same-named stops on real data (confirmed: agency 10's 公会堂前,
    two unrelated locations ~3km apart, merged into one dot). This pins the
    tuned eps (~550m) actually rejecting a same-name pair well beyond it."""
    app, agency_id = map_app
    pool = app.state.pool
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO static_stops (agency_id, stop_id, stop_name, stop_lat, stop_lon, geom) "
            "VALUES ($1, 'FA', '遠方前', 41.0, 141.000, ST_SetSRID(ST_MakePoint(141.000, 41.0), 4326)), "
            "       ($1, 'FB', '遠方前', 41.0, 141.010, ST_SetSRID(ST_MakePoint(141.010, 41.0), 4326))",
            agency_id,
        )
        await conn.execute(
            "INSERT INTO agg_stop_daily (agency_id, stop_id, date, service_type, time_band, delay_sum, samples) "
            "VALUES ($1,'FA','2026-06-06','weekday','morning',60,1), "
            "       ($1,'FB','2026-06-06','weekday','morning',180,1)",
            agency_id,
        )
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/{agency_id}/delays/heatmap?from=2026-06-06&to=2026-06-06")
    assert resp.status_code == 200
    feats = resp.json()["features"]
    assert len(feats) == 2, feats  # would be 1 under an oversized eps


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
    baseline: optional (avg_min, p90_min, samples) -> inserted into agg_route_stats.

    Seeds raw `updates` (for the trips/stop-profile drilldowns, which still read
    them) AND the precomputed `agg_route_daily` row the route-summary endpoint now
    reads — computed here from day_rows rather than via a full analyze(), so the
    hand-set baseline in agg_route_stats isn't clobbered. analyze()'s own builder
    is covered separately by test_analyze_builds_agg_route_daily."""
    from datetime import datetime, time, timezone

    seeded_at = datetime(2026, 6, 9, 10, 0, 0, tzinfo=timezone.utc)
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
                seeded_at,
                trip_id,
                service_type,
                sched,
                route_code,
                seq,
                delay,
            )
        # The route-summary endpoint reads agg_route_daily; mirror what analyze()
        # would compute for this day (rows are unique per (trip, stop) here).
        delays = [d for (_, _, d, _) in day_rows]
        await conn.execute(
            "INSERT INTO agg_route_daily (agency_id, date, route_code, service_type, "
            "avg_delay_sec, worst_delay_sec, trips_observed, samples, last_seen_at) "
            "VALUES ($1, DATE '2026-06-09', $2, $3, $4, $5, $6, $7, $8)",
            agency_id,
            route_code,
            service_type,
            round(sum(delays) / len(delays)),
            max(delays),
            len({t for (t, _, _, _) in day_rows}),
            len(delays),
            seeded_at,
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
async def test_route_summary_null_service_uses_route_grain_baseline(map_app):
    """A NULL-service daily row (stored as '') has no per-(route,service_type)
    baseline, but the route has typed history — it should fall back to the
    route-grain baseline instead of being stuck as no_baseline (Hiroshima case)."""
    app, agency_id = map_app
    pool = app.state.pool
    # Today's row is NULL-service ('') and clearly anomalous (420s vs 360s p90).
    await _seed_route(pool, agency_id, "R_NULLSVC", "", [(f"x{i}", 1, 420, "10:00") for i in range(40)], baseline=None)
    # The route DOES have a typed (平日) baseline in agg_route_stats.
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO agg_route_stats (agency_id, route_code, service_type, avg_min, p90_min, samples) "
            "VALUES ($1, 'R_NULLSVC', '平日', 2.0, 6.0, 500)",
            agency_id,
        )
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/{agency_id}/today/route-summary")
    r = {x["route_code"]: x for x in resp.json()["routes"]}["R_NULLSVC"]
    assert r["has_baseline"] is True
    assert r["baseline_avg_sec"] == 120  # route-grain 2.0 min, not no_baseline
    assert r["bucket"] == "anomaly"


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
    from datetime import date, datetime, time, timedelta, timezone

    # Anchored to "yesterday" (not a fixed calendar date) so this stays inside
    # the heatmap endpoint's default 30-day-ending-today window indefinitely —
    # a hardcoded past date silently ages out of that window as real time passes.
    captured_at = datetime.combine(date.today() - timedelta(days=1), time(8, 10), tzinfo=timezone.utc)
    async with pool.acquire() as c:
        await c.execute(
            "INSERT INTO static_stops (agency_id, stop_id, stop_name, geom) "
            "VALUES ($1,'s1','駅前',ST_SetSRID(ST_MakePoint(140.74,40.82),4326))",
            agency_id,
        )
        await c.execute(
            "INSERT INTO static_stop_times (agency_id, trip_id, stop_sequence, stop_id) VALUES ($1,'T',1,'s1')",
            agency_id,
        )
        for i, d in enumerate([60, 90, 121]):
            await c.execute(
                "INSERT INTO updates (agency_id, file_name, captured_at, trip_id, service_type, "
                "scheduled_time, route_code, stop_sequence, dep_delay) "
                "VALUES ($1,$2,$3,'T','平日',$4,'R1',1,$5)",
                agency_id,
                f"f{i}.pb",
                captured_at,
                time(8, 10),
                d,
            )


def _run_analyze(agency_id, ch_client):
    """analyze()'s dedup materialization now reads ClickHouse (Task 6); every
    test in this file seeds Postgres `updates` directly (pre-dating that
    migration), so mirror the same rows into ClickHouse first — see
    tests.conftest.mirror_updates_to_ch."""
    import os

    import psycopg2

    from pipeline.analyze import analyze
    from tests.conftest import mirror_updates_to_ch

    mirror_updates_to_ch(ch_client, agency_id)
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    try:
        analyze(agency_id, conn, ch_client)
        conn.commit()
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_heatmap_agg_path_averages_deduped_observations(map_app, ch_client):
    """No route filter -> aggregate path, averaging DEDUPED observations.

    Three DISTINCT trips (T1/T2/T3) serve stop s1 once each, delays [60, 90, 121]:
    each is its own dedup group, so all three survive → samples=3, mean = 271/3 =
    90.33s / 60 = 1.5055 -> ROUND(...,2) = 1.51. Guards two things: (1) float (not
    integer) division in the avg — 271//3//60 would give 1.50; (2) distinct trips
    are NOT collapsed by the dedup (only repeated polls of the SAME event are)."""
    from datetime import date, datetime, time, timedelta, timezone

    # Anchored to "yesterday" (not a fixed calendar date) — see _seed_heatmap
    # for why: a hardcoded past date silently ages out of the heatmap
    # endpoint's default 30-day-ending-today window as real time passes.
    captured_at = datetime.combine(date.today() - timedelta(days=1), time(8, 10), tzinfo=timezone.utc)
    app, agency_id = map_app
    async with app.state.pool.acquire() as c:
        await c.execute(
            "INSERT INTO static_stops (agency_id, stop_id, stop_name, geom) "
            "VALUES ($1,'s1','駅前',ST_SetSRID(ST_MakePoint(140.74,40.82),4326))",
            agency_id,
        )
        for trip, d in [("T1", 60), ("T2", 90), ("T3", 121)]:
            await c.execute(
                "INSERT INTO static_stop_times (agency_id, trip_id, stop_sequence, stop_id) VALUES ($1,$2,1,'s1')",
                agency_id,
                trip,
            )
            await c.execute(
                "INSERT INTO updates (agency_id, file_name, captured_at, trip_id, service_type, "
                " scheduled_time, route_code, stop_sequence, dep_delay) "
                "VALUES ($1,$2,$3,$4,'平日',$5,'R1',1,$6)",
                agency_id,
                f"{trip}.pb",
                captured_at,
                trip,
                time(8, 10),
                d,
            )
    _run_analyze(agency_id, ch_client)  # populate agg_stop_daily + agg_stop_routes
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/{agency_id}/delays/heatmap")
    assert resp.status_code == 200
    feats = resp.json()["features"]
    assert len(feats) == 1
    p = feats[0]["properties"]
    assert p["samples"] == 3  # three distinct trips, each a separate observation
    assert p["avg_delay_min"] == 1.51  # 271/3/60 rounded; guards float division
    assert "R1" in p["route_codes"]


@pytest.mark.asyncio
async def test_analyze_builds_agg_route_daily(map_app, ch_client):
    """analyze() populates agg_route_daily from raw updates, and route-summary
    reads it end-to-end (no raw scan)."""
    from datetime import datetime, time, timezone

    app, agency_id = map_app
    pool = app.state.pool
    async with pool.acquire() as conn:
        # route R1, 平日, 06-09: trip A stops 1&2 (60,120s), trip B stop 1 (600s)
        for i, (trip, seq, d) in enumerate([("A", 1, 60), ("A", 2, 120), ("B", 1, 600)]):
            await conn.execute(
                "INSERT INTO updates (agency_id, file_name, captured_at, trip_id, "
                "service_type, scheduled_time, route_code, stop_sequence, dep_delay) "
                "VALUES ($1,$2,$3,$4,'平日',$5,'R1',$6,$7)",
                agency_id,
                f"f{i}.pb",
                datetime(2026, 6, 9, 10, 0, tzinfo=timezone.utc),
                trip,
                time(10, 0),
                seq,
                d,
            )
    _run_analyze(agency_id, ch_client)
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM agg_route_daily WHERE agency_id=$1 AND route_code='R1'", agency_id)
    assert row is not None
    assert row["service_type"] == "平日"
    assert row["avg_delay_sec"] == 260  # (60+120+600)/3
    assert row["worst_delay_sec"] == 600
    assert row["trips_observed"] == 2  # A, B
    assert row["samples"] == 3
    # last_seen_at comes from include_captured_at on the dedup
    assert row["last_seen_at"] == datetime(2026, 6, 9, 10, 0, tzinfo=timezone.utc)

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/{agency_id}/today/route-summary")
    assert resp.status_code == 200
    body = resp.json()
    assert body["date"] == "2026-06-09"
    assert body["latest_captured_at"] == "2026-06-09T10:00:00+00:00"
    r1 = next(r for r in body["routes"] if r["route_code"] == "R1")
    assert r1["avg_delay_sec"] == 260
    assert r1["worst_delay_sec"] == 600
    assert r1["trips_observed"] == 2


@pytest.mark.asyncio
async def test_route_summary_keeps_null_service_routes(map_app, ch_client):
    """NULL service_type routes (no typed baseline) must still surface in triage —
    the old raw endpoint never filtered them, so the agg path must not either."""
    from datetime import datetime, time, timezone

    app, agency_id = map_app
    pool = app.state.pool
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO updates (agency_id, file_name, captured_at, trip_id, "
            "service_type, scheduled_time, route_code, stop_sequence, dep_delay) "
            "VALUES ($1,'f.pb',$2,'T1',NULL,$3,'R_NULL',1,1680)",
            agency_id,
            datetime(2026, 6, 9, 10, 0, tzinfo=timezone.utc),
            time(10, 0),
        )
    _run_analyze(agency_id, ch_client)
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/{agency_id}/today/route-summary")
    assert resp.status_code == 200
    r = next((x for x in resp.json()["routes"] if x["route_code"] == "R_NULL"), None)
    assert r is not None, "NULL-service route was dropped from triage"
    assert r["service_type"] is None  # '' sentinel mapped back to None
    assert r["worst_delay_sec"] == 1680
    assert r["bucket"] == "no_baseline"  # no typed baseline for NULL service
    assert r["has_baseline"] is False


@pytest.mark.asyncio
async def test_analyze_agg_route_daily_uses_sql_rounding(map_app, ch_client):
    """avg_delay_sec rounds half-away-from-zero (SQL ROUND), not Python banker's
    rounding — guards the builder's rounding if anyone reimplements it in Python."""
    from datetime import datetime, time, timezone

    app, agency_id = map_app
    pool = app.state.pool
    async with pool.acquire() as conn:
        for i, (trip, seq, d) in enumerate([("Z", 1, 2), ("Z", 2, 3)]):  # avg 2.5
            await conn.execute(
                "INSERT INTO updates (agency_id, file_name, captured_at, trip_id, "
                "service_type, scheduled_time, route_code, stop_sequence, dep_delay) "
                "VALUES ($1,$2,$3,$4,'平日',$5,'R_RND',$6,$7)",
                agency_id,
                f"f{i}.pb",
                datetime(2026, 6, 9, 10, 0, tzinfo=timezone.utc),
                trip,
                time(10, 0),
                seq,
                d,
            )
    _run_analyze(agency_id, ch_client)
    async with pool.acquire() as conn:
        avg = await conn.fetchval(
            "SELECT avg_delay_sec FROM agg_route_daily WHERE agency_id=$1 AND route_code='R_RND'",
            agency_id,
        )
    assert avg == 3  # ROUND(2.5) = 3 (SQL), not 2 (Python banker's)


@pytest.mark.asyncio
async def test_analyze_collapses_null_and_empty_service_type(map_app, ch_client):
    """A route with both a NULL and a genuine '' service_type on one day must
    collapse to ONE agg row, not abort analyze on a duplicate PK. The builder
    projects COALESCE(service_type,'') (NULL and '' both -> ''), so the GROUP BY
    must group on the same COALESCE — grouping on the raw column yields two groups
    that project to the same (agency_id, date, route_code, '') key and violate the
    agg_route_daily PK, rolling back every agg table for the agency."""
    from datetime import datetime, time, timezone

    app, agency_id = map_app
    pool = app.state.pool
    async with pool.acquire() as conn:
        for trip, svc, d in [("T1", None, 100), ("T2", "", 200)]:
            await conn.execute(
                "INSERT INTO updates (agency_id, file_name, captured_at, trip_id, "
                "service_type, scheduled_time, route_code, stop_sequence, dep_delay) "
                "VALUES ($1,$2,$3,$4,$5,$6,'R_DUP',1,$7)",
                agency_id,
                f"{trip}.pb",
                datetime(2026, 6, 9, 10, 0, tzinfo=timezone.utc),
                trip,
                svc,
                time(10, 0),
                d,
            )
    _run_analyze(agency_id, ch_client)  # must not raise UniqueViolation
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM agg_route_daily WHERE agency_id=$1 AND route_code='R_DUP'",
            agency_id,
        )
    assert len(rows) == 1  # NULL and '' collapsed into one bucket
    assert rows[0]["service_type"] == ""  # the sentinel
    assert rows[0]["avg_delay_sec"] == 150  # (100+200)/2 across both
    assert rows[0]["worst_delay_sec"] == 200
    assert rows[0]["trips_observed"] == 2  # T1, T2


@pytest.mark.asyncio
async def test_route_summary_exposes_feed_health(map_app, ch_client):
    """route-summary surfaces the per-day clamp/raw counts from agg_feed_health
    for the latest analyzed date (powers FeedHealthBanner)."""
    from datetime import datetime, time, timezone

    from pipeline.db import MAX_PLAUSIBLE_DELAY_SEC

    app, agency_id = map_app
    pool = app.state.pool
    async with pool.acquire() as conn:
        # one normal + one implausible spike on the same date → analyze builds
        # agg_route_daily (normal survives dedup/clamp) AND agg_feed_health (counts both raw)
        for i, d in enumerate([120, MAX_PLAUSIBLE_DELAY_SEC + 1]):
            await conn.execute(
                "INSERT INTO updates (agency_id, file_name, captured_at, trip_id, "
                "service_type, scheduled_time, route_code, stop_sequence, dep_delay) "
                "VALUES ($1,$2,$3,$4,'平日',$5,'R1',1,$6)",
                agency_id,
                f"f{i}.pb",
                datetime(2026, 6, 9, 8, 10, tzinfo=timezone.utc),
                f"T{i}",
                time(8, 10),
                d,
            )
    _run_analyze(agency_id, ch_client)
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/{agency_id}/today/route-summary")
    assert resp.status_code == 200
    body = resp.json()
    assert body["raw_samples"] == 2  # both raw observations
    assert body["clamp_count"] == 1  # the spike


@pytest.mark.asyncio
async def test_route_summary_feed_health_uses_7day_window(map_app, ch_client):
    """Feed-health sums the last 7 days, so a spike on an EARLIER day still shows
    even when the latest analyzed day is clean (frozen feeds recur across days)."""
    from datetime import datetime, time, timezone

    from pipeline.db import MAX_PLAUSIBLE_DELAY_SEC

    app, agency_id = map_app
    pool = app.state.pool
    async with pool.acquire() as conn:
        # spike on 06-07 (clamped out of agg_route_daily) ...
        await conn.execute(
            "INSERT INTO updates (agency_id, file_name, captured_at, trip_id, "
            "service_type, scheduled_time, route_code, stop_sequence, dep_delay) "
            "VALUES ($1,'spike.pb',$2,'TE','平日',$3,'R1',1,$4)",
            agency_id,
            datetime(2026, 6, 7, 8, 10, tzinfo=timezone.utc),
            time(8, 10),
            MAX_PLAUSIBLE_DELAY_SEC + 1,
        )
        # ... and a clean observation on the LATER, latest day 06-09
        await conn.execute(
            "INSERT INTO updates (agency_id, file_name, captured_at, trip_id, "
            "service_type, scheduled_time, route_code, stop_sequence, dep_delay) "
            "VALUES ($1,'ok.pb',$2,'TN','平日',$3,'R1',1,120)",
            agency_id,
            datetime(2026, 6, 9, 8, 10, tzinfo=timezone.utc),
            time(8, 10),
        )
    _run_analyze(agency_id, ch_client)
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/{agency_id}/today/route-summary")
    assert resp.status_code == 200
    body = resp.json()
    assert body["date"] == "2026-06-09"  # latest day is the clean one
    assert body["clamp_count"] == 1  # but the 06-07 spike is still surfaced (within 7 days)


@pytest.mark.asyncio
async def test_route_summary_returns_only_latest_date(map_app, ch_client):
    """route-summary serves MAX(date) only: a route present on an older day but
    not the latest must not leak into the response, and `date` is the latest."""
    from datetime import datetime, time, timezone

    app, agency_id = map_app
    pool = app.state.pool
    async with pool.acquire() as conn:
        # R_OLD only on 06-08; R_NEW only on 06-09 (the latest)
        for day, route in [(8, "R_OLD"), (9, "R_NEW")]:
            await conn.execute(
                "INSERT INTO updates (agency_id, file_name, captured_at, trip_id, "
                "service_type, scheduled_time, route_code, stop_sequence, dep_delay) "
                "VALUES ($1,$2,$3,'T1','平日',$4,$5,1,300)",
                agency_id,
                f"{route}.pb",
                datetime(2026, 6, day, 10, 0, tzinfo=timezone.utc),
                time(10, 0),
                route,
            )
    _run_analyze(agency_id, ch_client)
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/{agency_id}/today/route-summary")
    assert resp.status_code == 200
    body = resp.json()
    assert body["date"] == "2026-06-09"
    codes = {r["route_code"] for r in body["routes"]}
    assert codes == {"R_NEW"}  # R_OLD (older date only) excluded


@pytest.mark.asyncio
async def test_heatmap_route_filter_reads_agg_not_raw(map_app, ch_client):
    """Route filter -> agg_route_stop_daily, NOT raw updates. Without analyze the
    agg is empty -> 0 features (proving the source switched off the live path);
    after analyze, one dot whose 3 same-event polls dedup to a single observation
    (samples == 1)."""
    app, agency_id = map_app
    await _seed_heatmap(app.state.pool, agency_id)  # raw updates seeded; agg not yet built
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/{agency_id}/delays/heatmap?routes=R1")
        assert resp.status_code == 200
        assert resp.json()["features"] == []  # not live: raw updates ignored

        _run_analyze(agency_id, ch_client)
        resp = await client.get(f"/api/{agency_id}/delays/heatmap?routes=R1")
    assert resp.status_code == 200
    feats = resp.json()["features"]
    assert len(feats) == 1
    # 3 polls of one trip-stop event dedup to a single observation in the agg
    assert feats[0]["properties"]["samples"] == 1


@pytest.mark.asyncio
async def test_heatmap_agg_path_reads_agg_not_raw(map_app):
    """No route filter + agg NOT built -> 0 features, proving the source is
    agg_stop_daily (empty), not raw updates (which has 3 rows)."""
    app, agency_id = map_app
    await _seed_heatmap(app.state.pool, agency_id)  # raw updates seeded; NO _run_analyze
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/{agency_id}/delays/heatmap")
    assert resp.status_code == 200
    assert resp.json()["features"] == []
