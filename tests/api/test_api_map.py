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


@pytest.fixture
async def map_app_ch(map_app, ch_async_client):
    """`map_app` with `app.state.ch_client` wired to a real async ClickHouse
    client (Task 8: /delays/live, /route-shape, /today/route-summary,
    /today/route/*/trips and /today/route/*/stop-profile now read live
    `updates` from ClickHouse instead of Postgres). Tests using this fixture
    require `make ch-test` / RUN_CH_INTEGRATION=1 (via `ch_async_client`)."""
    app, agency_id = map_app
    app.state.ch_client = ch_async_client
    yield app, agency_id


@pytest.fixture
async def map_client_ch(map_app_ch):
    app, agency_id = map_app_ch
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, agency_id


@pytest.mark.asyncio
async def test_live_delays_empty(map_client_ch):
    client, agency_id = map_client_ch
    resp = await client.get(f"/api/{agency_id}/delays/live")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload == {"latest_captured_at": None, "rows": []}


@pytest.mark.asyncio
async def test_live_delays_unknown_agency(map_client_ch):
    client, _ = map_client_ch
    resp = await client.get("/api/99999/delays/live")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_live_delays_is_rate_limited_after_repeated_requests(map_client_ch):
    """map.py's endpoints must be throttled like every other public read
    router (overview/network/reports/ask) — unauthenticated callers can
    otherwise hit the heaviest live-scan endpoints without limit. Doesn't
    assert the exact configured threshold, only that the protection exists."""
    client, agency_id = map_client_ch
    statuses = []
    for _ in range(65):
        resp = await client.get(f"/api/{agency_id}/delays/live")
        statuses.append(resp.status_code)
        if resp.status_code == 429:
            break
    assert 429 in statuses, f"never throttled after 65 requests: {statuses}"


@pytest.mark.asyncio
async def test_live_delays_tiebreaks_same_poll_rows_by_lowest_stop_sequence(map_app_ch, ch_client):
    """A single GTFS-RT poll commonly reports dep_delay for more than one of
    a trip's upcoming stops at once (confirmed on real data: a propagated
    delay estimate spanning several future StopTimeUpdates in one TripUpdate
    message). live_delays dedups by trip_id ALONE (no stop_sequence in its
    group key), so two such rows tie on the argMax rewrite's dedup key
    (captured_at, file_name) -- `-toInt32(stop_sequence)` in the tiebreak
    tuple breaks that residual tie deterministically: the LOWEST
    stop_sequence (the soonest upcoming stop) wins, not whatever a bare
    GROUP BY happens to return first."""
    app, agency_id = map_app_ch
    pool = app.state.pool
    async with pool.acquire() as conn:
        # Same poll: identical captured_at (both NOW() calls resolve to the
        # same transaction timestamp) AND identical file_name, two different
        # stop_sequences of the SAME trip.
        await conn.execute(
            "INSERT INTO updates (agency_id, trip_id, route_code, stop_sequence, dep_delay, captured_at, "
            "file_name, service_type, scheduled_time) "
            "VALUES ($1, 'T_TIE', 'R_TIE', 1, 60, NOW(), 'same.pb', 'weekday', '10:05:00'), "
            "       ($1, 'T_TIE', 'R_TIE', 2, 300, NOW(), 'same.pb', 'weekday', '10:15:00')",
            agency_id,
        )
    from tests.conftest import mirror_updates_to_ch

    mirror_updates_to_ch(ch_client, agency_id)

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/{agency_id}/delays/live")
    assert resp.status_code == 200
    rows = resp.json()["rows"]
    row = next(r for r in rows if r["trip_id"] == "T_TIE")
    # stop_sequence isn't in the response, but its winning values are:
    # stop_sequence=1 (the lower one) must win the tie, not stop_sequence=2.
    assert row["dep_delay"] == 60
    assert row["scheduled_time"] == "10:05:00"
    assert row["route_code"] == "R_TIE"


@pytest.mark.asyncio
async def test_live_delays_normalizes_5char_scheduled_time_to_hhmmss(map_app_ch, ch_client):
    """aomori_regex-strategy agencies write a 5-char "HH:MM" scheduled_time
    (no seconds) to ClickHouse, unlike static_join's 8-char "HH:MM:SS" (see
    pipeline/strategies/aomori_regex.py). Before Postgres's TIME column was
    replaced by a plain ClickHouse String, every agency's wire format was
    uniform "HH:MM:SS" regardless of ingest strategy. This pins that
    /delays/live restores that uniform contract rather than exposing the
    per-strategy storage format to API consumers."""
    from pipeline.clickhouse import insert_updates

    app, agency_id = map_app_ch
    insert_updates(
        ch_client,
        agency_id=agency_id,
        rows=[("aomori.pb", "2026-05-09T10:00:00Z", "T_5CHAR", "weekday", "10:05", "R_5CHAR", 1, 45)],
    )

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/{agency_id}/delays/live")
    assert resp.status_code == 200
    rows = resp.json()["rows"]
    row = next(r for r in rows if r["trip_id"] == "T_5CHAR")
    assert row["scheduled_time"] == "10:05:00"


@pytest.mark.asyncio
async def test_live_delays_latest_day_has_no_non_null_delay(map_app_ch, ch_client):
    """Regression for a 500: the freshness probe (`latest_ts`) has no
    `dep_delay` filter, but the rows query adds `AND dep_delay IS NOT NULL`.
    When the agency's only/latest observation has a NULL dep_delay (routine —
    arrival-only StopTimeUpdates, or a degraded poll), `latest_ts` resolves
    to a real timestamp while the rows query legitimately matches zero rows.
    clickhouse-connect returns `column_names == ()` for that zero-row result,
    so deriving `cols.index("captured_at")` up front raised an unhandled
    `ValueError` -> 500. This differs from `test_live_delays_empty` (zero
    rows at all, which short-circuits on `latest_ts is None` before ever
    running the rows query) -- here `latest_ts` IS set, and the crash was in
    the second query's row-building code."""
    app, agency_id = map_app_ch
    pool = app.state.pool
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO updates (agency_id, trip_id, route_code, stop_sequence, dep_delay, captured_at, "
            "file_name, service_type, scheduled_time) "
            "VALUES ($1, 'T_NULL', 'R_NULL', 1, NULL, NOW(), 'null.pb', 'weekday', '10:05:00')",
            agency_id,
        )
    from tests.conftest import mirror_updates_to_ch

    mirror_updates_to_ch(ch_client, agency_id)

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/{agency_id}/delays/live")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["rows"] == []
    assert payload["latest_captured_at"] is not None


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


async def _seed_route_existence(conn, agency_id, route_code, service_type="weekday"):
    """Minimal agg_route_daily row so route_shape/route_trips/route_stop_profile's
    existence precheck (map.py) passes for route_code -- real
    avg/worst/trips/samples figures don't matter here, only that a row
    exists. Checks agg_route_daily specifically, not agg_route_stats: the
    latter is built with a NOT NULL service_type filter (pipeline/analyze.py),
    making it a lossy existence oracle, whereas agg_route_daily (what
    today_route_summary's route list is built from) has no such filter."""
    await conn.execute(
        "INSERT INTO agg_route_daily (agency_id, date, route_code, service_type, "
        "avg_delay_sec, worst_delay_sec, trips_observed, samples, last_seen_at, sum_delay_sec) "
        "VALUES ($1, CURRENT_DATE, $2, $3, 0, 0, 1, 1, NOW(), 0)",
        agency_id,
        route_code,
        service_type,
    )


@pytest.mark.asyncio
async def test_route_shape_returns_geometry_when_shapes_loaded(map_app_ch, ch_client):
    """When static_trips.shape_id resolves to a static_shapes row, the
    endpoint returns a GeoJSON LineString."""
    app, agency_id = map_app_ch
    pool = app.state.pool
    async with pool.acquire() as conn:
        await _seed_route_existence(conn, agency_id, "R1")
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
    from tests.conftest import mirror_updates_to_ch

    mirror_updates_to_ch(ch_client, agency_id)

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


@pytest.mark.asyncio
async def test_route_shape_falls_back_to_bounded_window_shape_when_ctx_window_is_empty(map_app_ch, ch_client):
    """Pre-ClickHouse-migration behavior: a route with real observations,
    all outside the caller's ctx window (default: last 30 days), must still
    render its topology (geometry + unobserved_stops) even though there is
    zero delay data to show for the selected window.

    The shape-vote query is bounded by ctx so it stops scanning the route's
    entire history on every request. That has a side effect: if the ctx
    window itself has zero observations for the route (e.g. it only ran on
    days outside the
    selected range), the ctx-bounded dedup query returns nothing, so there
    is no shape-vote signal and `chosen_shape_id` stays None — geometry and
    unobserved_stops silently go empty too, even though pre-migration the
    endpoint would still draw the route from its all-time shape. The
    fallback shape-vote query (fired only on this empty-window edge case)
    restores that behavior — bounded to the last 30 days off the agency's
    OWN latest captured_at (not an unbounded all-time scan: the bound must
    be tight enough to actually exclude older history, not just wide enough
    to look bounded on paper).
    The endpoint's existence precheck (at the top of the function, ahead of
    ANY ClickHouse call, not just this fallback) means a fabricated
    route_code never reaches ClickHouse at all -- R1 needs an agg_route_daily
    row to pass it. The 60-day-old seed below is still within 30 days of the
    AGENCY's latest activity (its own captured_at, the only row this agency
    has), so it's still found."""
    app, agency_id = map_app_ch
    pool = app.state.pool
    async with pool.acquire() as conn:
        await _seed_route_existence(conn, agency_id, "R1")
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
        # Observations exist for this route, but all 60 days ago — well
        # outside the endpoint's default 30-day ctx window.
        await conn.execute(
            "INSERT INTO updates (agency_id, trip_id, route_code, stop_sequence, dep_delay, captured_at, "
            "file_name, service_type, scheduled_time) "
            "VALUES ($1, 'T1', 'R1', 1, 60, NOW() - INTERVAL '60 days', 'test.pb', 'weekday', '09:00:00'), "
            "       ($1, 'T1', 'R1', 2, 90, NOW() - INTERVAL '60 days', 'test.pb', 'weekday', '09:05:00')",
            agency_id,
        )
        await conn.execute(
            "INSERT INTO static_shapes (agency_id, shape_id, geom) "
            "VALUES ($1, 'S1', "
            "ST_SetSRID(ST_MakeLine(ARRAY[ST_MakePoint(140.74, 40.82), ST_MakePoint(140.75, 40.83)]), 4326))",
            agency_id,
        )
    from tests.conftest import mirror_updates_to_ch

    mirror_updates_to_ch(ch_client, agency_id)

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # No from/to -> default ctx (last 30 days), which excludes the
        # 60-day-old observations seeded above.
        resp = await client.get(f"/api/{agency_id}/route-shape?route=R1")

    assert resp.status_code == 200
    body = resp.json()
    assert body["route"] == "R1"
    # Delay data is correctly empty for the (empty) selected window.
    assert body["stops"] == [], body
    # But topology still renders from the bounded fallback shape vote.
    assert body["geometry"] is not None, body
    assert body["geometry"]["type"] == "LineString"
    assert len(body["unobserved_stops"]) >= 2, body


@pytest.mark.asyncio
async def test_route_shape_shape_vote_ignores_null_delay_only_trips(map_app_ch, ch_client):
    """The shape-vote's per-trip weights are now derived from the dedup
    query's own rows (perf(map) b16fd70), which are filtered by `dep_delay
    IS NOT NULL` before the argMax GROUP BY (trip_id, stop_sequence) dedup —
    the same filter-before-dedup ordering used everywhere else in this
    codebase (see `pipeline/db.py::build_dedup_ch_sql`). A trip whose every observed
    StopTimeUpdate is arrival-only (no `dep_delay` — common at a route's
    terminal stop in GTFS-RT) therefore contributes ZERO weight to the vote,
    not the full raw-row count the old separate `COUNT(*)` query would have
    given it. This is a disclosed, accepted trade-off, not a bug — but the
    vote must still land on the shape with real weighted support rather
    than getting thrown off (e.g. picking the NULL-only shape, or None)
    by the presence of arrival-only trips on a competing shape variant.

    Fixture: shape S1 has two trips (T1, T2) with real dep_delay data over
    three stops each (weight 6); shape S2 has one trip (T3) that is
    arrival-only -- every one of its rows has NULL dep_delay, so it
    contributes zero rows to the dedup scan and zero vote weight. The vote
    must still pick S1 (the only shape with any weight), and the returned
    per-stop stats must reflect only T1/T2's real delay data."""
    app, agency_id = map_app_ch
    pool = app.state.pool
    async with pool.acquire() as conn:
        await _seed_route_existence(conn, agency_id, "R1")
        await conn.execute(
            "INSERT INTO static_trips (agency_id, trip_id, route_id, shape_id) "
            "VALUES ($1, 'T1', 'R1', 'S1'), ($1, 'T2', 'R1', 'S1'), ($1, 'T3', 'R1', 'S2')",
            agency_id,
        )
        await conn.execute(
            "INSERT INTO static_stops (agency_id, stop_id, stop_name, stop_lat, stop_lon, geom) "
            "VALUES ($1, 'ST1', 'A', 40.80, 140.70, ST_SetSRID(ST_MakePoint(140.70, 40.80), 4326)), "
            "       ($1, 'ST2', 'B', 40.81, 140.71, ST_SetSRID(ST_MakePoint(140.71, 40.81), 4326)), "
            "       ($1, 'ST3', 'C', 40.82, 140.72, ST_SetSRID(ST_MakePoint(140.72, 40.82), 4326)), "
            "       ($1, 'ST4', 'D', 45.00, 150.00, ST_SetSRID(ST_MakePoint(150.00, 45.00), 4326)), "
            "       ($1, 'ST5', 'E', 45.01, 150.01, ST_SetSRID(ST_MakePoint(150.01, 45.01), 4326))",
            agency_id,
        )
        await conn.execute(
            "INSERT INTO static_stop_times (agency_id, trip_id, stop_sequence, stop_id, arrival_time, departure_time) "
            "VALUES "
            "($1, 'T1', 1, 'ST1', '09:00:00', '09:00:00'), "
            "($1, 'T1', 2, 'ST2', '09:05:00', '09:05:00'), "
            "($1, 'T1', 3, 'ST3', '09:10:00', '09:10:00'), "
            "($1, 'T2', 1, 'ST1', '10:00:00', '10:00:00'), "
            "($1, 'T2', 2, 'ST2', '10:05:00', '10:05:00'), "
            "($1, 'T2', 3, 'ST3', '10:10:00', '10:10:00'), "
            "($1, 'T3', 1, 'ST4', '11:00:00', '11:00:00'), "
            "($1, 'T3', 2, 'ST5', '11:05:00', '11:05:00')",
            agency_id,
        )
        # T1/T2: real dep_delay data (shape S1). T3: NULL dep_delay on every
        # row (arrival-only StopTimeUpdates, no departure delay ever
        # reported) -- shape S2's only trip, so S2 gets zero vote weight.
        await conn.execute(
            "INSERT INTO updates (agency_id, trip_id, route_code, stop_sequence, dep_delay, captured_at, "
            "file_name, service_type, scheduled_time) VALUES "
            "($1, 'T1', 'R1', 1, 30, NOW(), 'f1.pb', 'weekday', '09:00:00'), "
            "($1, 'T1', 'R1', 2, 60, NOW(), 'f1.pb', 'weekday', '09:05:00'), "
            "($1, 'T1', 'R1', 3, 90, NOW(), 'f1.pb', 'weekday', '09:10:00'), "
            "($1, 'T2', 'R1', 1, 40, NOW(), 'f2.pb', 'weekday', '10:00:00'), "
            "($1, 'T2', 'R1', 2, 70, NOW(), 'f2.pb', 'weekday', '10:05:00'), "
            "($1, 'T2', 'R1', 3, 100, NOW(), 'f2.pb', 'weekday', '10:10:00'), "
            "($1, 'T3', 'R1', 1, NULL, NOW(), 'f3.pb', 'weekday', '11:00:00'), "
            "($1, 'T3', 'R1', 2, NULL, NOW(), 'f3.pb', 'weekday', '11:05:00')",
            agency_id,
        )
        await conn.execute(
            "INSERT INTO static_shapes (agency_id, shape_id, geom) VALUES "
            "($1, 'S1', ST_SetSRID(ST_MakeLine(ARRAY["
            "ST_MakePoint(140.70, 40.80), ST_MakePoint(140.71, 40.81), ST_MakePoint(140.72, 40.82)]), 4326)), "
            "($1, 'S2', ST_SetSRID(ST_MakeLine(ARRAY["
            "ST_MakePoint(150.00, 45.00), ST_MakePoint(150.01, 45.01)]), 4326))",
            agency_id,
        )
    from tests.conftest import mirror_updates_to_ch

    mirror_updates_to_ch(ch_client, agency_id)

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/{agency_id}/route-shape?route=R1")

    assert resp.status_code == 200
    body = resp.json()
    # The vote landed on S1 (real weighted support), not S2 (all-NULL, zero
    # weight) and not None -- confirmed via S1's distinctive coordinates.
    assert body["geometry"] is not None, body
    coords = body["geometry"]["coordinates"]
    assert coords[0] == [140.70, 40.80], body
    assert coords[-1] == [140.72, 40.82], body
    # Per-stop stats reflect only T1/T2's real delay data (2 samples/stop);
    # T3's arrival-only rows never entered the dedup scan at all.
    stops_by_seq = {s["stop_sequence"]: s for s in body["stops"]}
    assert set(stops_by_seq) == {1, 2, 3}, body
    for s in stops_by_seq.values():
        assert s["samples"] == 2, body


@pytest.mark.asyncio
async def test_route_shape_vote_tie_break_is_deterministic(map_app_ch, ch_client):
    """Two shape variants with EQUAL vote weight (one trip each, same number
    of deduped stop-events) must render in the same deterministic order on
    every request, not whichever `shape_link_rows`' unordered Postgres query
    happens to return first. Both variants render as a MultiLineString
    (each is a real observed 系統 for this route_code), ordered by the same
    tie-break key used to pin `chosen_shape_id` for stops
    (`(shape_counts[sid], sid)` descending) — the lexicographically LARGER
    id (`S_Z` > `S_A`) must always sort first here."""
    app, agency_id = map_app_ch
    pool = app.state.pool
    async with pool.acquire() as conn:
        await _seed_route_existence(conn, agency_id, "R_TIE2")
        await conn.execute(
            "INSERT INTO static_trips (agency_id, trip_id, route_id, shape_id) "
            "VALUES ($1, 'TA', 'R_TIE2', 'S_A'), ($1, 'TZ', 'R_TIE2', 'S_Z')",
            agency_id,
        )
        await conn.execute(
            "INSERT INTO static_shapes (agency_id, shape_id, geom) VALUES "
            "($1, 'S_A', ST_SetSRID(ST_MakeLine(ARRAY[ST_MakePoint(10.0, 10.0), ST_MakePoint(10.1, 10.1)]), 4326)), "
            "($1, 'S_Z', ST_SetSRID(ST_MakeLine(ARRAY[ST_MakePoint(20.0, 20.0), ST_MakePoint(20.1, 20.1)]), 4326))",
            agency_id,
        )
        # Equal weight: one trip per shape, one dep_delay-bearing row each.
        await conn.execute(
            "INSERT INTO updates (agency_id, trip_id, route_code, stop_sequence, dep_delay, captured_at, "
            "file_name, service_type, scheduled_time) VALUES "
            "($1, 'TA', 'R_TIE2', 1, 30, NOW(), 'a.pb', 'weekday', '09:00:00'), "
            "($1, 'TZ', 'R_TIE2', 1, 30, NOW(), 'z.pb', 'weekday', '09:00:00')",
            agency_id,
        )
    from tests.conftest import mirror_updates_to_ch

    mirror_updates_to_ch(ch_client, agency_id)

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/{agency_id}/route-shape?route=R_TIE2")
    assert resp.status_code == 200
    body = resp.json()
    assert body["geometry"] is not None, body
    # Both observed variants render; S_Z (winner of the tie-break) sorts
    # first, S_A second — deterministic regardless of Postgres row order.
    assert body["geometry"]["type"] == "MultiLineString", body
    assert body["geometry"]["coordinates"] == [
        [[20.0, 20.0], [20.1, 20.1]],
        [[10.0, 10.0], [10.1, 10.1]],
    ], body


class _ExplodingChClient:
    """Stand-in ``ch`` that fails the test if route_shape ever reaches
    ClickHouse -- proves the agg_route_daily existence precheck at the TOP
    of the function short-circuits before the ctx-bounded dedup query, not
    just that the two happen to produce the same output. A prior version of
    this precheck sat inside the empty-window fallback branch instead, so a
    fabricated route_code under a wide ctx window still paid for the
    ctx-bounded dedup query's full cost before ever reaching the precheck --
    an assertion on the response body alone can't tell those two placements
    apart."""

    async def query(self, *args, **kwargs):
        raise AssertionError("agg_route_daily precheck should have short-circuited before any ClickHouse query")


@pytest.mark.asyncio
async def test_route_shape_returns_empty_for_nonexistent_route(map_app):
    """A fabricated/never-observed route_code must resolve to the same empty
    shape as any other "not found" response, without ever touching ClickHouse
    (see _ExplodingChClient). No agency/CH seeding at all: this agency has
    zero agg_route_daily rows."""
    app, agency_id = map_app
    app.state.ch_client = _ExplodingChClient()
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/{agency_id}/route-shape?route=NOPE&from=2025-01-01&to=2026-12-31")
    assert resp.status_code == 200
    assert resp.json() == {"route": "NOPE", "geometry": None, "stops": [], "unobserved_stops": []}


async def _seed_route(pool, agency_id, route_code, service_type, day_rows, baseline=None, ch_client=None):
    """day_rows: list of (trip_id, stop_sequence, dep_delay_sec, scheduled_time).
    baseline: optional (avg_min, p90_min, samples) -> inserted into agg_route_stats.
    Always inserts an agg_route_stats row for (agency_id, route_code,
    service_type), even when baseline is None (NULL avg_min/p90_min/samples
    in that case) — needed for route-summary's baseline-lookup tests, not for
    existence: route_trips/route_stop_profile/route_shape's existence
    precheck (map.py's anonymous-scan hardening) checks agg_route_daily, not
    agg_route_stats (the latter is a lossy existence oracle — see
    _seed_route_existence's docstring) — which the unconditional
    agg_route_daily insert below already covers for any route seeded via
    this helper.
    ch_client: optional sync ClickHouse client — when given, the raw rows
    seeded into Postgres `updates` below are ALSO mirrored into ClickHouse
    (via tests.conftest.mirror_updates_to_ch) since the trips/stop-profile
    drilldowns and route-summary's freshness header now read live `updates`
    from ClickHouse (Task 8), not Postgres.

    Seeds raw `updates` (for the trips/stop-profile drilldowns, which read
    them from ClickHouse when `ch_client` is given) AND the precomputed
    `agg_route_daily` row the route-summary endpoint now reads — computed
    here from day_rows rather than via a full analyze(), so the hand-set
    baseline in agg_route_stats isn't clobbered. analyze()'s own builder
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
            "avg_delay_sec, worst_delay_sec, trips_observed, samples, last_seen_at, sum_delay_sec) "
            "VALUES ($1, DATE '2026-06-09', $2, $3, $4, $5, $6, $7, $8, $9)",
            agency_id,
            route_code,
            service_type,
            round(sum(delays) / len(delays)),
            max(delays),
            len({t for (t, _, _, _) in day_rows}),
            len(delays),
            seeded_at,
            sum(delays),
        )
        avg_min, p90_min, samples = baseline if baseline is not None else (None, None, None)
        # sum_delay_sec is the exact seconds-sum backing avg_min, needed by
        # the route-summary endpoint's route-grain (rb) baseline fallback
        # (SUM(sum_delay_sec)/SUM(samples)) -- a row missing it (NULL) is
        # correctly excluded from that pool, exactly like a real pre-backfill
        # row, so it must be set here whenever avg_min/samples are given.
        sum_delay_sec = round(avg_min * 60 * samples) if avg_min is not None else None
        await conn.execute(
            "INSERT INTO agg_route_stats (agency_id, route_code, service_type, "
            "avg_min, p90_min, samples, sum_delay_sec) VALUES ($1,$2,$3,$4,$5,$6,$7)",
            agency_id,
            route_code,
            service_type,
            avg_min,
            p90_min,
            samples,
            sum_delay_sec,
        )
    if ch_client is not None:
        from tests.conftest import mirror_updates_to_ch

        mirror_updates_to_ch(ch_client, agency_id)


@pytest.mark.asyncio
async def test_route_summary_buckets_and_deviation(map_app_ch, ch_client):
    app, agency_id = map_app_ch
    pool = app.state.pool
    # Anomaly: today avg 420s, baseline avg 120s (2min) p90 360s (6min), 40 samples
    await _seed_route(
        pool,
        agency_id,
        "R_ANOM",
        "平日",
        [(f"t{i}", 1, 420, "10:00") for i in range(40)],
        baseline=(2.0, 6.0, 500),
        ch_client=ch_client,
    )
    # No baseline route
    await _seed_route(
        pool,
        agency_id,
        "R_NOBASE",
        "平日",
        [(f"n{i}", 1, 300, "11:00") for i in range(40)],
        baseline=None,
        ch_client=ch_client,
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
    assert anom["baseline_samples"] == 500
    assert anom["deviation_sec"] == 300  # 420 - 120
    assert anom["low_confidence"] is False

    nobase = routes["R_NOBASE"]
    assert nobase["bucket"] == "no_baseline"
    assert nobase["has_baseline"] is False
    assert nobase["baseline_avg_sec"] is None
    assert nobase["deviation_sec"] is None


@pytest.mark.asyncio
async def test_route_summary_null_service_uses_route_grain_baseline(map_app_ch, ch_client):
    """A NULL-service daily row (stored as '') has no per-(route,service_type)
    baseline, but the route has typed history — it should fall back to the
    route-grain baseline instead of being stuck as no_baseline (Hiroshima case)."""
    app, agency_id = map_app_ch
    pool = app.state.pool
    # Today's row is NULL-service ('') and clearly anomalous (420s vs 360s p90).
    await _seed_route(
        pool,
        agency_id,
        "R_NULLSVC",
        "",
        [(f"x{i}", 1, 420, "10:00") for i in range(40)],
        baseline=None,
        ch_client=ch_client,
    )
    # The route DOES have a typed (平日) baseline in agg_route_stats.
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO agg_route_stats (agency_id, route_code, service_type, avg_min, p90_min, "
            "samples, sum_delay_sec) VALUES ($1, 'R_NULLSVC', '平日', 2.0, 6.0, 500, 60000)",
            agency_id,
        )
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/{agency_id}/today/route-summary")
    r = {x["route_code"]: x for x in resp.json()["routes"]}["R_NULLSVC"]
    assert r["has_baseline"] is True
    assert r["baseline_avg_sec"] == 120  # route-grain 2.0 min, not no_baseline
    assert r["baseline_samples"] == 500  # pooled SUM(samples) across service_types
    assert r["bucket"] == "anomaly"


@pytest.mark.asyncio
async def test_route_summary_baseline_columns_stay_same_source(map_app_ch, ch_client):
    """agg_route_stats no longer gates out thin (route, service_type) groups, so
    a group's p90_min can itself be null (every contributing row's dep_delay
    was itself NULL) while avg_min isn't. baseline_avg_min/baseline_p90_min/
    baseline_samples must all come from the SAME source (the exact (route,
    service_type) match, here) rather than independently falling back column-by-
    column to the route-grain pooled baseline -- even though the route DOES have
    a real, non-null p90 available from a different service_type's pooled figure,
    it must not be silently substituted in."""
    app, agency_id = map_app_ch
    pool = app.state.pool
    # Exact-match baseline for today's own service_type has a null p90 (thin
    # group) but a real avg and sample count.
    await _seed_route(
        pool,
        agency_id,
        "R_THINP90",
        "平日",
        [(f"y{i}", 1, 420, "10:00") for i in range(40)],
        baseline=(2.0, None, 1),
        ch_client=ch_client,
    )
    # A different service_type's baseline for the SAME route has a real,
    # non-null p90 -- this populates the route-grain pooled fallback (rb) with
    # a value that must NOT leak into baseline_p90_sec above.
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO agg_route_stats (agency_id, route_code, service_type, avg_min, p90_min, "
            "samples, sum_delay_sec) VALUES ($1, 'R_THINP90', '土日', 2.0, 6.0, 500, 60000)",
            agency_id,
        )
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/{agency_id}/today/route-summary")
    r = {x["route_code"]: x for x in resp.json()["routes"]}["R_THINP90"]
    assert r["baseline_avg_sec"] == 120  # exact-match avg (b), not the pooled rb figure
    assert r["baseline_p90_sec"] is None  # must stay null, not rb's pooled 360
    assert r["baseline_samples"] == 1  # exact-match samples (b), not rb's pooled 501
    assert r["bucket"] == "no_baseline"  # classify_route treats a null p90 as no baseline
    # has_baseline must track the bucket, not just baseline_avg_sec: a thin
    # group can have a real avg but a null p90 (no_baseline bucket) -- showing
    # has_baseline=True here would render a contradictory "baseline pending"
    # + concrete today-vs-baseline comparison for the same row.
    assert r["has_baseline"] is False


@pytest.mark.asyncio
async def test_route_summary_pooled_p90_ignores_null_p90_rows(map_app_ch, ch_client):
    """The route-grain pooled fallback (rb)'s base_p90_min must not be diluted
    by a contributing service_type whose own p90_min is null. SUM(p90_min *
    samples) silently skips a null numerator term, but the denominator must be
    FILTERed the same way, or that row's samples still count against a p90 it
    contributed nothing to -- biasing the pooled figure down."""
    app, agency_id = map_app_ch
    pool = app.state.pool
    # NULL-service today row: no exact (route, service_type) match, so this
    # must go through the rb pooled fallback.
    await _seed_route(
        pool,
        agency_id,
        "R_POOLMIX",
        "",
        [(f"z{i}", 1, 480, "10:00") for i in range(40)],
        baseline=None,
        ch_client=ch_client,
    )
    async with pool.acquire() as conn:
        # Thin/degenerate contributor: real avg, null p90, small samples.
        await conn.execute(
            "INSERT INTO agg_route_stats (agency_id, route_code, service_type, avg_min, p90_min, "
            "samples, sum_delay_sec) VALUES ($1, 'R_POOLMIX', '平日', 2.0, NULL, 3, 360)",
            agency_id,
        )
        # Healthy contributor: real avg and p90.
        await conn.execute(
            "INSERT INTO agg_route_stats (agency_id, route_code, service_type, avg_min, p90_min, "
            "samples, sum_delay_sec) VALUES ($1, 'R_POOLMIX', '土日', 4.0, 10.0, 500, 120000)",
            agency_id,
        )
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/{agency_id}/today/route-summary")
    r = {x["route_code"]: x for x in resp.json()["routes"]}["R_POOLMIX"]
    # Must equal the healthy group's own p90 exactly (the only contributor) --
    # NOT diluted to 10.0*500/503*60 ~= 596s by including the null-p90 row's
    # samples in the denominator.
    assert r["baseline_p90_sec"] == 600


@pytest.mark.asyncio
async def test_route_summary_route_grain_baseline_pools_exact_sum_delay_sec(map_app_ch, ch_client):
    """The rb CTE's base_avg_min must pool via SUM(sum_delay_sec)/SUM(samples)
    (exact), not SUM(avg_min * samples)/SUM(samples) (a sample-weighted mean
    of already-rounded per-service_type avg_min values, biased away from the
    true pooled mean -- migration 0028's rationale, mirrored here for the
    route-grain baseline the same way pipeline/digest/build.py's
    _ROUTE_BASELINE_SQL already does).

    Two contributing service_types are seeded whose stored avg_min diverges
    sharply from what sum_delay_sec/samples backs (500%: 5.0 vs the true 1.0),
    so the two pooling methods land on unambiguously different results --
    proving which one the endpoint actually uses, not just measuring a subtle
    rounding-scale difference that could get lost in later second-rounding.
    """
    app, agency_id = map_app_ch
    pool = app.state.pool
    # NULL-service today row: no exact (route, service_type) match, so this
    # must go through the rb pooled fallback.
    await _seed_route(
        pool,
        agency_id,
        "R_EXACTPOOL",
        "",
        [(f"e{i}", 1, 420, "10:00") for i in range(40)],
        baseline=None,
        ch_client=ch_client,
    )
    async with pool.acquire() as conn:
        # avg_min=1.0, samples=10 -> exact sum_delay_sec=600 (consistent).
        await conn.execute(
            "INSERT INTO agg_route_stats (agency_id, route_code, service_type, avg_min, p90_min, "
            "samples, sum_delay_sec) VALUES ($1, 'R_EXACTPOOL', '平日', 1.0, 3.0, 10, 600)",
            agency_id,
        )
        # avg_min=5.0 (stored), samples=1000, but sum_delay_sec=60000 -- the
        # TRUE mean this row backs is 60000/1000/60 = 1.0 min, not 5.0.
        await conn.execute(
            "INSERT INTO agg_route_stats (agency_id, route_code, service_type, avg_min, p90_min, "
            "samples, sum_delay_sec) VALUES ($1, 'R_EXACTPOOL', '土日', 5.0, 3.0, 1000, 60000)",
            agency_id,
        )
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/{agency_id}/today/route-summary")
    r = {x["route_code"]: x for x in resp.json()["routes"]}["R_EXACTPOOL"]
    # Exact: (600 + 60000) / 60 / (10 + 1000) = 1010 / 1010 = 1.0 min = 60s.
    # The old biased pattern would instead give (1.0*10 + 5.0*1000) / 1010 =
    # 4.9604 min ~= 298s -- an unambiguously different answer.
    assert r["baseline_avg_sec"] == 60
    assert r["baseline_samples"] == 1010


@pytest.mark.asyncio
async def test_route_summary_low_confidence_caps_anomaly(map_app_ch, ch_client):
    app, agency_id = map_app_ch
    pool = app.state.pool
    # Would be anomaly (avg 420 > p90 360) but only 5 obs -> watch + low_confidence
    await _seed_route(
        pool,
        agency_id,
        "R_THIN",
        "平日",
        [(f"thin{i}", 1, 420, "10:00") for i in range(5)],
        baseline=(2.0, 6.0, 500),
        ch_client=ch_client,
    )
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/{agency_id}/today/route-summary")
    r = next(x for x in resp.json()["routes"] if x["route_code"] == "R_THIN")
    assert r["bucket"] == "watch"
    assert r["low_confidence"] is True


@pytest.mark.asyncio
async def test_route_trips_drilldown(map_app_ch, ch_client):
    app, agency_id = map_app_ch
    pool = app.state.pool
    # trip A: two stops, delays 600 & 540 -> avg 570; trip B: one stop, 120
    await _seed_route(
        pool,
        agency_id,
        "R_DRILL",
        "平日",
        [("A", 1, 600, "08:40"), ("A", 2, 540, "08:40"), ("B", 1, 120, "12:05")],
        ch_client=ch_client,
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
async def test_route_trips_empty_when_no_data(map_client_ch):
    client, agency_id = map_client_ch
    resp = await client.get(f"/api/{agency_id}/today/route/NOPE/trips")
    assert resp.status_code == 200
    assert resp.json() == {"date": None, "trips": []}


@pytest.mark.asyncio
async def test_route_trips_excludes_stale_route_beyond_bound(map_app_ch, ch_client):
    """A route that DOES exist (has an agg_route_daily row, so it passes the
    existence precheck) but whose only ClickHouse observations are older
    than the 30-day bound anchored to the agency's own latest activity must
    resolve to the empty response, not resurrect that stale data as if it
    were "today's". Regression for the pre-bound behavior, which scanned all
    history and would have returned the 60-day-old trip as current."""
    app, agency_id = map_app_ch
    pool = app.state.pool
    async with pool.acquire() as conn:
        # Sets this agency's latest captured_at to "now" via an unrelated route.
        await conn.execute(
            "INSERT INTO updates (agency_id, trip_id, route_code, stop_sequence, dep_delay, captured_at, "
            "file_name, service_type, scheduled_time) "
            "VALUES ($1, 'T_OTHER', 'R_OTHER', 1, 10, NOW(), 'other.pb', 'weekday', '08:00:00')",
            agency_id,
        )
        # R_STALE exists (has an agg_route_daily row, so it passes the
        # precheck) but its only observations are 60 days old -- outside the
        # 30-day bound anchored to the agency's latest activity seeded above.
        await _seed_route_existence(conn, agency_id, "R_STALE")
        await conn.execute(
            "INSERT INTO updates (agency_id, trip_id, route_code, stop_sequence, dep_delay, captured_at, "
            "file_name, service_type, scheduled_time) "
            "VALUES ($1, 'T_STALE', 'R_STALE', 1, 600, NOW() - INTERVAL '60 days', 'stale.pb', 'weekday', '09:00:00')",
            agency_id,
        )
    from tests.conftest import mirror_updates_to_ch

    mirror_updates_to_ch(ch_client, agency_id)

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/{agency_id}/today/route/R_STALE/trips")
    assert resp.status_code == 200
    assert resp.json() == {"date": None, "trips": []}


@pytest.mark.asyncio
async def test_route_stop_profile_empty_when_no_data(map_client_ch):
    """Characterization test (slice 3 refactor baseline): a fabricated/never-
    observed route_code resolves to the empty response, mirroring
    test_route_trips_empty_when_no_data. Added because this branch of
    route_stop_profile had no direct test before this slice, despite sharing
    the exact existence-precheck + bounded-probe logic route_trips already
    covers."""
    client, agency_id = map_client_ch
    resp = await client.get(f"/api/{agency_id}/today/route/NOPE/stop-profile")
    assert resp.status_code == 200
    assert resp.json() == {"date": None, "stops": []}


@pytest.mark.asyncio
async def test_route_stop_profile_excludes_stale_route_beyond_bound(map_app_ch, ch_client):
    """Characterization test (slice 3 refactor baseline): a route that exists
    (has an agg_route_daily row) but whose only ClickHouse observations are
    older than the 30-day bound anchored to the agency's own latest activity
    must resolve to the empty response, mirroring
    test_route_trips_excludes_stale_route_beyond_bound. Added because this
    branch of route_stop_profile had no direct test before this slice."""
    app, agency_id = map_app_ch
    pool = app.state.pool
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO updates (agency_id, trip_id, route_code, stop_sequence, dep_delay, captured_at, "
            "file_name, service_type, scheduled_time) "
            "VALUES ($1, 'T_OTHER', 'R_OTHER', 1, 10, NOW(), 'other.pb', 'weekday', '08:00:00')",
            agency_id,
        )
        await _seed_route_existence(conn, agency_id, "R_STALE_SP")
        await conn.execute(
            "INSERT INTO updates (agency_id, trip_id, route_code, stop_sequence, dep_delay, captured_at, "
            "file_name, service_type, scheduled_time) "
            "VALUES ($1, 'T_STALE', 'R_STALE_SP', 1, 600, NOW() - INTERVAL '60 days', "
            "'stale.pb', 'weekday', '09:00:00')",
            agency_id,
        )
    from tests.conftest import mirror_updates_to_ch

    mirror_updates_to_ch(ch_client, agency_id)

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/{agency_id}/today/route/R_STALE_SP/stop-profile")
    assert resp.status_code == 200
    assert resp.json() == {"date": None, "stops": []}


@pytest.mark.asyncio
async def test_route_stop_profile_drilldown(map_app_ch, ch_client):
    app, agency_id = map_app_ch
    pool = app.state.pool
    # seq 1: delays 60 & 120 -> avg 90; seq 2: 600 -> avg 600 (bottleneck)
    await _seed_route(
        pool,
        agency_id,
        "R_PROF",
        "平日",
        [("A", 1, 60, "08:40"), ("B", 1, 120, "09:00"), ("A", 2, 600, "08:40")],
        ch_client=ch_client,
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
async def test_route_shape_returns_null_geometry_when_no_shapes_loaded(map_app_ch, ch_client):
    """If trips have a shape_id but static_shapes has no matching row,
    geometry is null and stops are still populated."""
    app, agency_id = map_app_ch
    pool = app.state.pool
    async with pool.acquire() as conn:
        await _seed_route_existence(conn, agency_id, "R1")
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
    from tests.conftest import mirror_updates_to_ch

    mirror_updates_to_ch(ch_client, agency_id)

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/{agency_id}/route-shape?route=R1")

    assert resp.status_code == 200
    body = resp.json()
    assert body["geometry"] is None, body
    assert len(body["stops"]) >= 2


@pytest.mark.asyncio
async def test_route_shape_returns_stops_when_no_trip_has_a_shape_id(map_app_ch, ch_client):
    """shapes.txt is optional in GTFS -- an agency that never loaded one has
    static_trips.shape_id NULL for every trip, so chosen_shape_id is always
    None. Regression: gating the per-stop stats query on chosen_shape_id
    (an earlier version of the route_shape query-bounding fix did) silently
    dropped `stops` to `[]` for every route on such an agency -- main only
    ever used shape_id to PIN stops to one variant when multiple existed,
    never to gate whether stats ran at all."""
    app, agency_id = map_app_ch
    pool = app.state.pool
    async with pool.acquire() as conn:
        await _seed_route_existence(conn, agency_id, "R1")
        # No shape_id on this trip at all (NULL, not just unresolved geometry).
        await conn.execute(
            "INSERT INTO static_trips (agency_id, trip_id, route_id, shape_id) VALUES ($1, 'T1', 'R1', NULL)",
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
    from tests.conftest import mirror_updates_to_ch

    mirror_updates_to_ch(ch_client, agency_id)

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/{agency_id}/route-shape?route=R1")

    assert resp.status_code == 200
    body = resp.json()
    assert body["geometry"] is None, body
    assert len(body["stops"]) >= 2, "stats query must run even with no shape_id at all"
    assert {s["avg_min"] for s in body["stops"]} != {None}


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
    tests.conftest.mirror_updates_to_ch.

    Pins `SET TIME ZONE 'Asia/Tokyo'` on the analyze connection, matching
    every real analyze() caller (gtfs_pipeline._get_conn, the cron endpoint)
    — without it, this connection defaults to UTC, which happened to mask a
    real bug: analyze() bulk-loading ClickHouse's naive-UTC captured_at
    values straight into a timestamptz column is only safe under a UTC
    session; under the JST session production actually uses, it silently
    shifted every captured_at (and last_seen_at) by 9 hours."""
    import os

    import psycopg2

    from pipeline.analyze import analyze
    from tests.conftest import mirror_updates_to_ch

    mirror_updates_to_ch(ch_client, agency_id)
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    try:
        with conn.cursor() as cur:
            cur.execute("SET TIME ZONE 'Asia/Tokyo'")
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
async def test_analyze_builds_agg_route_daily(map_app, ch_client, ch_async_client):
    """analyze() populates agg_route_daily from raw updates, and route-summary
    reads it end-to-end (no raw scan)."""
    from datetime import datetime, time, timezone

    app, agency_id = map_app
    app.state.ch_client = ch_async_client
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
async def test_route_summary_degrades_when_clickhouse_freshness_probe_fails(map_app):
    """Fix B regression: ClickHouse backs ONLY the informational
    ``latest_captured_at`` freshness header here — every actual route row
    comes from Postgres ``agg_route_daily``. A ClickHouse hiccup on that one
    probe must degrade to ``latest_captured_at: null``, not 500 the whole
    endpoint (no real ClickHouse needed for this — a client whose `.query`
    always raises is enough to simulate the hiccup)."""

    class _BrokenCh:
        async def query(self, *args, **kwargs):
            raise RuntimeError("simulated ClickHouse outage")

    app, agency_id = map_app
    app.state.ch_client = _BrokenCh()
    pool = app.state.pool
    await _seed_route(
        pool,
        agency_id,
        "R1",
        "平日",
        [(f"t{i}", 1, 300, "10:00") for i in range(25)],
        baseline=None,
        ch_client=None,
    )
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/{agency_id}/today/route-summary")
    assert resp.status_code == 200
    body = resp.json()
    assert body["latest_captured_at"] is None
    r1 = next(r for r in body["routes"] if r["route_code"] == "R1")
    assert r1["avg_delay_sec"] == 300
    assert r1["samples"] == 25


@pytest.mark.asyncio
async def test_route_summary_keeps_null_service_routes(map_app, ch_client, ch_async_client):
    """NULL service_type routes (no typed baseline) must still surface in triage —
    the old raw endpoint never filtered them, so the agg path must not either."""
    from datetime import datetime, time, timezone

    app, agency_id = map_app
    app.state.ch_client = ch_async_client
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
async def test_analyze_agg_route_daily_uses_sql_rounding(map_app, ch_client, ch_async_client):
    """avg_delay_sec rounds half-away-from-zero (SQL ROUND), not Python banker's
    rounding — guards the builder's rounding if anyone reimplements it in Python."""
    from datetime import datetime, time, timezone

    app, agency_id = map_app
    app.state.ch_client = ch_async_client
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
async def test_analyze_collapses_null_and_empty_service_type(map_app, ch_client, ch_async_client):
    """A route with both a NULL and a genuine '' service_type on one day must
    collapse to ONE agg row, not abort analyze on a duplicate PK. The builder
    projects COALESCE(service_type,'') (NULL and '' both -> ''), so the GROUP BY
    must group on the same COALESCE — grouping on the raw column yields two groups
    that project to the same (agency_id, date, route_code, '') key and violate the
    agg_route_daily PK, rolling back every agg table for the agency."""
    from datetime import datetime, time, timezone

    app, agency_id = map_app
    app.state.ch_client = ch_async_client
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
async def test_route_summary_exposes_feed_health(map_app, ch_client, ch_async_client):
    """route-summary surfaces the per-day clamp/raw counts from agg_feed_health
    for the latest analyzed date (powers FeedHealthBanner)."""
    from datetime import datetime, time, timezone

    from pipeline.db import MAX_PLAUSIBLE_DELAY_SEC

    app, agency_id = map_app
    app.state.ch_client = ch_async_client
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
async def test_route_summary_feed_health_uses_7day_window(map_app, ch_client, ch_async_client):
    """Feed-health sums the last 7 days, so a spike on an EARLIER day still shows
    even when the latest analyzed day is clean (frozen feeds recur across days)."""
    from datetime import datetime, time, timezone

    from pipeline.db import MAX_PLAUSIBLE_DELAY_SEC

    app, agency_id = map_app
    app.state.ch_client = ch_async_client
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
async def test_route_summary_returns_only_latest_date(map_app, ch_client, ch_async_client):
    """route-summary serves MAX(date) only: a route present on an older day but
    not the latest must not leak into the response, and `date` is the latest."""
    from datetime import datetime, time, timezone

    app, agency_id = map_app
    app.state.ch_client = ch_async_client
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
