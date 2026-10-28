import os
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from httpx import ASGITransport

from api.middleware.ratelimit import limiter
from api.range import jst_today
from pipeline.clickhouse import LIVE_TABLE
from tests.conftest import _test_pool
from tests.fixtures.clock import jst_midday
from tests.fixtures.stop_clusters import rebuild_stop_clusters


@pytest.fixture(autouse=True)
def _reset_limiter():
    limiter.reset()
    yield


@pytest.fixture
async def map_app(apply_schema):
    from api.main import app

    pool = await _test_pool()
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
            "TRUNCATE agencies, updates, static_stops, stop_clusters, static_stop_times, "
            "static_trips, static_routes, static_calendar_dates, static_shapes, "
            "agg_route_stats, agg_route_hour, "
            "agg_daily_trend, agg_route_daily, agg_stop_daily, agg_stop_routes, "
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
    client. /delays/live, /route-shape, /today/route-summary,
    /today/route/*/trips and /today/route/*/stop-profile read live `updates`
    from ClickHouse rather than Postgres, so tests using this fixture require
    `make ch-test` / RUN_CH_INTEGRATION=1 (via `ch_async_client`)."""
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
    assert payload == {"latest_captured_at": None, "rows": [], "truncated": False}


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

    mirror_updates_to_ch(ch_client, agency_id, table=LIVE_TABLE)

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/{agency_id}/delays/live")
    assert resp.status_code == 200
    rows = resp.json()["rows"]
    row = next(r for r in rows if r["trip_id"] == "T_TIE")
    assert row["stop_sequence"] == 1
    assert row["dep_delay"] == 60
    assert row["scheduled_time"] == "10:05:00"
    assert row["route_code"] == "R_TIE"


@pytest.mark.asyncio
async def test_live_delays_says_when_more_trips_reported_than_it_returns(map_app_ch, ch_client):
    app, agency_id = map_app_ch
    async with app.state.pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO updates (agency_id, trip_id, route_code, stop_sequence, dep_delay, captured_at, "
            "file_name, service_type, scheduled_time) "
            "SELECT $1, 'T' || n, 'R', 1, 60 * n, NOW(), 'one.pb', 'weekday', '10:00:00' FROM generate_series(1, 3) n",
            agency_id,
        )
    from tests.conftest import mirror_updates_to_ch

    mirror_updates_to_ch(ch_client, agency_id, table=LIVE_TABLE)
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        capped = (await client.get(f"/api/{agency_id}/delays/live", params={"limit": 2})).json()
        whole = (await client.get(f"/api/{agency_id}/delays/live", params={"limit": 3})).json()
    assert [r["trip_id"] for r in capped["rows"]] == ["T1", "T2"]
    assert capped["truncated"] is True
    assert len(whole["rows"]) == 3
    assert whole["truncated"] is False


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
        rows=[("aomori.pb", datetime.now(timezone.utc), "T_5CHAR", "weekday", "10:05", "R_5CHAR", 1, 45)],
        table=LIVE_TABLE,
    )

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/{agency_id}/delays/live")
    assert resp.status_code == 200
    rows = resp.json()["rows"]
    row = next(r for r in rows if r["trip_id"] == "T_5CHAR")
    assert row["scheduled_time"] == "10:05:00"


@pytest.mark.asyncio
async def test_live_delays_resolves_stop_location_from_static_schedule(map_app_ch, ch_client):
    from pipeline.clickhouse import insert_updates

    app, agency_id = map_app_ch
    async with app.state.pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO static_stops (agency_id, stop_id, stop_name, stop_lat, stop_lon, geom) "
            "VALUES ($1, 'S2', '中央駅', 40.8123, 140.7456, ST_SetSRID(ST_MakePoint(140.7456, 40.8123), 4326))",
            agency_id,
        )
        await conn.execute(
            "INSERT INTO static_trips (agency_id, trip_id, route_id, trip_headsign) "
            "VALUES ($1, 'T_STATIC', 'R_STATIC', '市役所前')",
            agency_id,
        )
        await conn.execute(
            "INSERT INTO static_stop_times (agency_id, trip_id, stop_sequence, stop_id) "
            "VALUES ($1, 'T_STATIC', 2, 'S2')",
            agency_id,
        )
    insert_updates(
        ch_client,
        agency_id=agency_id,
        rows=[("live.pb", datetime.now(timezone.utc), "T_STATIC", "weekday", "10:05", "R_STATIC", 2, 180)],
        table=LIVE_TABLE,
    )

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/{agency_id}/delays/live")

    assert resp.status_code == 200
    row = next(r for r in resp.json()["rows"] if r["trip_id"] == "T_STATIC")
    assert row["stop_id"] == "S2"
    assert row["stop_name"] == "中央駅"
    assert row["stop_lat"] == 40.8123
    assert row["stop_lon"] == 140.7456
    assert row["headsign"] == "市役所前"


@pytest.mark.asyncio
async def test_live_trip_progress_returns_nearest_reported_stop_trail(map_app_ch, ch_client):
    """Each poll contributes only its nearest upcoming stop, producing a
    compact report trail without presenting future StopTimeUpdates as GPS."""
    from pipeline.clickhouse import insert_updates

    app, agency_id = map_app_ch
    async with app.state.pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO static_trips (agency_id, trip_id, route_id, trip_headsign, direction_id) "
            "VALUES ($1, 'T_PROGRESS', 'R_PROGRESS', '新町', 1)",
            agency_id,
        )
        await conn.execute(
            "INSERT INTO static_stops (agency_id, stop_id, stop_name, stop_lat, stop_lon, geom) VALUES "
            "($1, 'S1', '中央病院', 40.81, 140.71, ST_SetSRID(ST_MakePoint(140.71, 40.81), 4326)), "
            "($1, 'S2', '市役所前', 40.82, 140.72, ST_SetSRID(ST_MakePoint(140.72, 40.82), 4326))",
            agency_id,
        )
        await conn.execute(
            "INSERT INTO static_stop_times (agency_id, trip_id, stop_sequence, stop_id) VALUES "
            "($1, 'T_PROGRESS', 1, 'S1'), ($1, 'T_PROGRESS', 2, 'S2')",
            agency_id,
        )
    t0 = datetime.now(timezone.utc) - timedelta(minutes=2)
    insert_updates(
        ch_client,
        agency_id=agency_id,
        rows=[
            ("poll-1.pb", t0, "T_PROGRESS", "weekday", "15:00", "R_PROGRESS", 1, 60),
            ("poll-1.pb", t0, "T_PROGRESS", "weekday", "15:05", "R_PROGRESS", 2, 240),
            ("poll-2.pb", t0 + timedelta(minutes=2), "T_PROGRESS", "weekday", "15:05", "R_PROGRESS", 2, 180),
        ],
        table=LIVE_TABLE,
    )

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/{agency_id}/delays/live-progress", params={"trip_id": "T_PROGRESS"})

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["route_code"] == "R_PROGRESS"
    assert payload["headsign"] == "新町"
    assert payload["direction_id"] == 1
    assert [(row["stop_sequence"], row["dep_delay"]) for row in payload["stops"]] == [(1, 60), (2, 180)]
    assert payload["stops"][0]["stop_name"] == "中央病院"
    assert payload["stops"][1]["stop_lon"] == 140.72


@pytest.mark.asyncio
async def test_live_delays_latest_day_has_no_non_null_delay(map_app_ch, ch_client):
    """A latest day with no non-NULL delay must not 500: the freshness probe
    (`latest_ts`) has no `dep_delay` filter, but the rows query adds
    `AND dep_delay IS NOT NULL`.
    When the agency's only/latest observation has a NULL dep_delay (routine —
    arrival-only StopTimeUpdates, or a degraded poll), `latest_ts` resolves
    to a real timestamp while the rows query legitimately matches zero rows.
    clickhouse-connect returns `column_names == ()` for that zero-row result,
    so deriving `cols.index("captured_at")` up front would raise an unhandled
    `ValueError` -> 500. This differs from `test_live_delays_empty` (zero
    rows at all, which short-circuits on `latest_ts is None` before ever
    running the rows query) -- here `latest_ts` IS set, and the failure point
    is the second query's row-building code."""
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

    mirror_updates_to_ch(ch_client, agency_id, table=LIVE_TABLE)

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/{agency_id}/delays/live")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["rows"] == []
    assert payload["latest_captured_at"] is not None


@pytest.mark.asyncio
async def test_the_live_screen_reads_updates_live_and_never_history(map_app_ch, ch_client):
    """A row in `updates` stamped now (history never holds one once the split
    ships, but the deploy day does) must not show as a current trip."""
    from pipeline.clickhouse import LIVE_TABLE, insert_updates

    app, agency_id = map_app_ch
    now = datetime.now(timezone.utc)
    insert_updates(
        ch_client, agency_id, [("oracle/x/live.pb", now, "T_LIVE", "平日", "10:05", "R1", 1, 60)], table=LIVE_TABLE
    )
    insert_updates(ch_client, agency_id, [("20261002/hist.pb", now, "T_HIST", "平日", "10:05", "R1", 1, 600)])
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        live = (await client.get(f"/api/{agency_id}/delays/live")).json()
        hist_trail = (await client.get(f"/api/{agency_id}/delays/live-progress", params={"trip_id": "T_HIST"})).json()
        live_trail = (await client.get(f"/api/{agency_id}/delays/live-progress", params={"trip_id": "T_LIVE"})).json()
    assert [r["trip_id"] for r in live["rows"]] == ["T_LIVE"]
    assert hist_trail["stops"] == []
    assert [s["dep_delay"] for s in live_trail["stops"]] == [60]


@pytest.mark.asyncio
async def test_todays_live_rows_reach_the_live_screen_and_no_aggregate(map_app_ch, ch_client):
    import psycopg2

    from pipeline.analyze import analyze
    from pipeline.clickhouse import LIVE_TABLE, insert_updates

    app, agency_id = map_app_ch
    insert_updates(
        ch_client,
        agency_id,
        [("oracle/x/1.pb", datetime.now(timezone.utc), "T1", "平日", "10:05", "R1", 1, 300)],
        table=LIVE_TABLE,
    )
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    try:
        with conn.cursor() as cur:
            cur.execute("SET TIME ZONE 'Asia/Tokyo'")
        analyze(agency_id, conn, ch_client)
        conn.commit()
    finally:
        conn.close()
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        live = (await client.get(f"/api/{agency_id}/delays/live")).json()
        heat = (await client.get(f"/api/{agency_id}/delays/heatmap")).json()
    assert [r["trip_id"] for r in live["rows"]] == ["T1"]
    assert heat["features"] == []
    assert await app.state.pool.fetchval("SELECT count(*) FROM agg_route_daily WHERE agency_id = $1", agency_id) == 0


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
    assert resp.json() == {"rows": []}


@pytest.mark.asyncio
async def test_stops_list_empty(map_client):
    client, agency_id = map_client
    resp = await client.get(f"/api/{agency_id}/stops")
    assert resp.status_code == 200
    assert resp.json() == {"rows": []}


@pytest.mark.asyncio
async def test_routes_list_exposes_every_documented_field(map_app):
    """A response model silently drops any field it does not name, so the
    catalogue's exact shape needs pinning rather than just its type."""
    app, agency_id = map_app
    async with app.state.pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO static_routes (agency_id, route_id, route_short_name, route_long_name) "
            "VALUES ($1, '国道・古川線(1021)', '1021', '国道・古川線')",
            agency_id,
        )
        await conn.execute(
            "INSERT INTO static_trips (agency_id, trip_id, route_id, service_id, trip_headsign) "
            "VALUES ($1, 'T1', '国道・古川線(1021)', 'S', '古川行')",
            agency_id,
        )
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(f"/api/{agency_id}/routes")
    assert resp.status_code == 200
    assert resp.json() == {
        "rows": [
            {
                "route_id": "国道・古川線(1021)",
                "route_short_name": "1021",
                "route_long_name": "国道・古川線",
                # Extracted from the parenthesised tail of route_id.
                "route_code": "1021",
                "trip_headsigns": ["古川行"],
                "origin_name": None,
                "via_name": None,
                "destination_name": None,
                "is_loop": False,
                "bidirectional": False,
            }
        ]
    }


@pytest.mark.asyncio
async def test_routes_list_carries_where_each_route_runs(map_app):
    app, agency_id = map_app
    async with app.state.pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO static_routes (agency_id, route_id, route_short_name, route_long_name, "
            "  origin_name, via_name, destination_name, is_loop, bidirectional) "
            "VALUES ($1, '1237145910', '54-1', '西広島バイパス線', '広島バスセンター', '市役所', '山田団地車庫前', "
            "        false, false), "
            "       ($1, '360868713', '3', '3号線', '広島駅', NULL, '観音新町三丁目', false, true), "
            "       ($1, '横内環状線(1101)', '1101', '横内環状線', '青森駅', NULL, '青森駅', true, false)",
            agency_id,
        )
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(f"/api/{agency_id}/routes")
    assert resp.status_code == 200
    fields = ("route_id", "origin_name", "via_name", "destination_name", "is_loop", "bidirectional")
    assert [tuple(r[f] for f in fields) for r in resp.json()["rows"]] == [
        ("1237145910", "広島バスセンター", "市役所", "山田団地車庫前", False, False),
        ("360868713", "広島駅", None, "観音新町三丁目", False, True),
        ("横内環状線(1101)", "青森駅", None, "青森駅", True, False),
    ]


@pytest.mark.asyncio
async def test_stops_list_exposes_every_documented_field(map_app):
    app, agency_id = map_app
    async with app.state.pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO static_stops (agency_id, stop_id, stop_name, stop_lat, stop_lon) "
            "VALUES ($1, 'S1', '駅前', 40.5, 140.5)",
            agency_id,
        )
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(f"/api/{agency_id}/stops")
    assert resp.status_code == 200
    assert resp.json() == {"rows": [{"stop_id": "S1", "stop_name": "駅前", "stop_lat": 40.5, "stop_lon": 140.5}]}


@pytest.mark.asyncio
async def test_stops_list_tolerates_a_stop_with_no_name_or_position(map_app):
    """GTFS makes stop_name and the coordinates optional, so the model must not
    reject a sparse row with a 500."""
    app, agency_id = map_app
    async with app.state.pool.acquire() as conn:
        await conn.execute("INSERT INTO static_stops (agency_id, stop_id) VALUES ($1, 'S2')", agency_id)
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(f"/api/{agency_id}/stops")
    assert resp.status_code == 200
    assert resp.json() == {"rows": [{"stop_id": "S2", "stop_name": None, "stop_lat": None, "stop_lon": None}]}


@pytest.mark.asyncio
async def test_stop_bounds_is_null_for_an_agency_without_located_stops(map_client):
    client, agency_id = map_client
    resp = await client.get(f"/api/{agency_id}/stops/bounds")
    assert resp.status_code == 200
    assert resp.json() == {"bounds": None}


@pytest.mark.asyncio
async def test_stop_bounds_spans_the_agencys_located_stops_only(map_app):
    """The Live map opens on this box, so a stop with no position, a (0, 0)
    placeholder, or another agency's stop must not stretch it."""
    app, agency_id = map_app
    async with app.state.pool.acquire() as conn:
        other = await conn.fetchval(
            "INSERT INTO agencies (agency_name, feed_url) VALUES ('other', 'http://other') RETURNING agency_id"
        )
        await conn.execute(
            "INSERT INTO static_stops (agency_id, stop_id, stop_lat, stop_lon) "
            "VALUES ($1, 'A', 34.39, 132.45), ($1, 'B', 34.67, 132.92), ($1, 'C', 34.10, 132.18), "
            "       ($1, 'ZERO', 0, 0), ($2, 'FAR', 40.82, 140.74)",
            agency_id,
            other,
        )
        await conn.execute("INSERT INTO static_stops (agency_id, stop_id) VALUES ($1, 'NOPOS')", agency_id)
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(f"/api/{agency_id}/stops/bounds")
    assert resp.status_code == 200
    assert resp.json() == {"bounds": [132.18, 34.10, 132.92, 34.67]}


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
        await rebuild_stop_clusters(conn, agency_id)
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
        await rebuild_stop_clusters(conn, agency_id)
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
    chain transitively — an `eps` sized like a kilometre-scale grid cell
    (rather than as an actual merge radius) bridges genuinely distant
    same-named stops on real data, merging two unrelated locations a few km
    apart into one dot. This pins the tuned eps (~550m) actually rejecting
    a same-name pair well beyond it."""
    app, agency_id = map_app
    pool = app.state.pool
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO static_stops (agency_id, stop_id, stop_name, stop_lat, stop_lon, geom) "
            "VALUES ($1, 'FA', '遠方前', 41.0, 141.000, ST_SetSRID(ST_MakePoint(141.000, 41.0), 4326)), "
            "       ($1, 'FB', '遠方前', 41.0, 141.010, ST_SetSRID(ST_MakePoint(141.010, 41.0), 4326))",
            agency_id,
        )
        await rebuild_stop_clusters(conn, agency_id)
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
async def test_heatmap_omits_a_stop_without_a_cluster_row(map_app):
    """stop_clusters is the dot set. A stop that reached static_stops outside
    load_static has no row and no dot — never a 500."""
    app, agency_id = map_app
    async with app.state.pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO static_stops (agency_id, stop_id, stop_name, stop_lat, stop_lon, geom) "
            "VALUES ($1, 'S_ORPHAN', '孤立', 40.0, 140.0, ST_SetSRID(ST_MakePoint(140.0, 40.0), 4326))",
            agency_id,
        )
        await conn.execute(
            "INSERT INTO agg_stop_daily (agency_id, stop_id, date, service_type, time_band, delay_sum, samples) "
            "VALUES ($1,'S_ORPHAN','2026-06-06','weekday','morning',60,1)",
            agency_id,
        )
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/{agency_id}/delays/heatmap?from=2026-06-06&to=2026-06-06")
    assert resp.status_code == 200
    assert resp.json()["features"] == []


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
    day = jst_today()
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
            "VALUES ($1, 'T1', 'R1', 1, 60, $2, 'test.pb', 'weekday', '09:00:00'), "
            "       ($1, 'T1', 'R1', 2, 90, $2, 'test.pb', 'weekday', '09:05:00')",
            agency_id,
            jst_midday(day),
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
        resp = await client.get(f"/api/{agency_id}/route-shape?route=R1&from={day}&to={day}")

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
    # Only the windowed vote fills the stops; the empty-window fallback returns none.
    assert [stop["stop_sequence"] for stop in body["stops"]] == [1, 2]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("accept_language", "unobserved_name", "observed_name"),
    [("ja", "2番停留所", "3番停留所"), ("en", "Stop #2", "Stop #3")],
)
async def test_route_shape_names_a_stop_with_no_stop_name_in_the_request_language(
    map_app_ch, ch_client, accept_language, unobserved_name, observed_name
):
    """A stop whose stops.txt row carries no name gets a sequence-based name in
    the request's language, observed or not, so the stop chart always has a label."""
    app, agency_id = map_app_ch
    day = jst_today()
    async with app.state.pool.acquire() as conn:
        await _seed_route_existence(conn, agency_id, "R1")
        await conn.execute(
            "INSERT INTO static_trips (agency_id, trip_id, route_id, shape_id) VALUES ($1, 'T1', 'R1', 'S1')",
            agency_id,
        )
        await conn.execute(
            "INSERT INTO static_stops (agency_id, stop_id, stop_name, stop_lat, stop_lon, geom) "
            "VALUES ($1, 'ST1', '駅前', 40.82, 140.74, ST_SetSRID(ST_MakePoint(140.74, 40.82), 4326)), "
            "       ($1, 'ST2', NULL, 40.83, 140.75, ST_SetSRID(ST_MakePoint(140.75, 40.83), 4326)), "
            "       ($1, 'ST3', NULL, 40.84, 140.76, ST_SetSRID(ST_MakePoint(140.76, 40.84), 4326))",
            agency_id,
        )
        await conn.execute(
            "INSERT INTO static_stop_times (agency_id, trip_id, stop_sequence, stop_id, arrival_time, departure_time) "
            "VALUES ($1, 'T1', 1, 'ST1', '09:00:00', '09:00:00'), "
            "       ($1, 'T1', 2, 'ST2', '09:05:00', '09:05:00'), "
            "       ($1, 'T1', 3, 'ST3', '09:10:00', '09:10:00')",
            agency_id,
        )
        await conn.execute(
            "INSERT INTO updates (agency_id, trip_id, route_code, stop_sequence, dep_delay, captured_at, "
            "file_name, service_type, scheduled_time) "
            "VALUES ($1, 'T1', 'R1', 1, 60, $2, 'test.pb', 'weekday', '09:00:00'), "
            "       ($1, 'T1', 'R1', 3, 90, $2, 'test.pb', 'weekday', '09:10:00')",
            agency_id,
            jst_midday(day),
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
        resp = await client.get(
            f"/api/{agency_id}/route-shape?route=R1&from={day}&to={day}",
            headers={"Accept-Language": accept_language},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert [(s["stop_sequence"], s["stop_name"]) for s in body["stops"]] == [(1, "駅前"), (3, observed_name)]
    assert [(s["stop_sequence"], s["stop_name"]) for s in body["unobserved_stops"]] == [(2, unobserved_name)]


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
    """The shape-vote's per-trip weights are derived from the dedup
    query's own rows, which are filtered by `dep_delay
    IS NOT NULL` before the argMax GROUP BY (trip_id, stop_sequence) dedup —
    the same filter-before-dedup ordering used everywhere else in this
    codebase (see `pipeline/db.py::build_dedup_ch_sql`). A trip whose every observed
    StopTimeUpdate is arrival-only (no `dep_delay` — common at a route's
    terminal stop in GTFS-RT) therefore contributes ZERO weight to the vote,
    not the full raw-row count a separate `COUNT(*)` query would give it.
    This is a disclosed, accepted trade-off, not a bug — but the
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
        # captured_at sits on a closed day: the default range is the last 30
        # CLOSED JST days, which excludes today.
        await conn.execute(
            "INSERT INTO updates (agency_id, trip_id, route_code, stop_sequence, dep_delay, captured_at, "
            "file_name, service_type, scheduled_time) VALUES "
            "($1, 'T1', 'R1', 1, 30, NOW() - INTERVAL '1 day', 'f1.pb', 'weekday', '09:00:00'), "
            "($1, 'T1', 'R1', 2, 60, NOW() - INTERVAL '1 day', 'f1.pb', 'weekday', '09:05:00'), "
            "($1, 'T1', 'R1', 3, 90, NOW() - INTERVAL '1 day', 'f1.pb', 'weekday', '09:10:00'), "
            "($1, 'T2', 'R1', 1, 40, NOW() - INTERVAL '1 day', 'f2.pb', 'weekday', '10:00:00'), "
            "($1, 'T2', 'R1', 2, 70, NOW() - INTERVAL '1 day', 'f2.pb', 'weekday', '10:05:00'), "
            "($1, 'T2', 'R1', 3, 100, NOW() - INTERVAL '1 day', 'f2.pb', 'weekday', '10:10:00'), "
            "($1, 'T3', 'R1', 1, NULL, NOW() - INTERVAL '1 day', 'f3.pb', 'weekday', '11:00:00'), "
            "($1, 'T3', 'R1', 2, NULL, NOW() - INTERVAL '1 day', 'f3.pb', 'weekday', '11:05:00')",
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
    day = jst_today()
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
            "($1, 'TA', 'R_TIE2', 1, 30, $2, 'a.pb', 'weekday', '09:00:00'), "
            "($1, 'TZ', 'R_TIE2', 1, 30, $2, 'z.pb', 'weekday', '09:00:00')",
            agency_id,
            jst_midday(day),
        )
    from tests.conftest import mirror_updates_to_ch

    mirror_updates_to_ch(ch_client, agency_id)

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/{agency_id}/route-shape?route=R_TIE2&from={day}&to={day}")
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
    just that the two happen to produce the same output. Were this precheck
    inside the empty-window fallback branch instead, a fabricated route_code
    under a wide ctx window would still pay for the ctx-bounded dedup
    query's full cost before ever reaching the precheck --
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
    body = resp.json()
    body.pop("scope_applied")
    assert body == {
        "route": "NOPE",
        "shape_id": None,
        "variants": [],
        "segments": [],
        "geometry": None,
        "stops": [],
        "unobserved_stops": [],
    }


async def _seed_route(
    pool, agency_id, route_code, service_type, day_rows, baseline=None, ch_client=None, seeded_at=None
):
    """day_rows: list of (trip_id, stop_sequence, dep_delay_sec, scheduled_time).
    baseline: optional (avg_min, p90_min, samples) -> inserted into agg_route_stats,
    for tests that need a baseline row present even though these drilldowns
    never read it themselves.
    Always inserts an agg_route_daily row for (agency_id, route_code,
    service_type): route_trips/route_stop_profile/route_shape's existence
    precheck (map.py's anonymous-scan hardening, `route_exists`) checks
    agg_route_daily, not agg_route_stats (the latter is a lossy existence
    oracle — see _seed_route_existence's docstring).
    ch_client: optional sync ClickHouse client — when given, the raw rows
    seeded into Postgres `updates` below are ALSO mirrored into ClickHouse
    (via tests.conftest.mirror_updates_to_ch) since the trips/stop-profile
    drilldowns read live `updates` from ClickHouse, not Postgres.

    Seeds raw `updates` (for the trips/stop-profile drilldowns, which read
    them from ClickHouse when `ch_client` is given) AND the precomputed
    `agg_route_daily` row `route_exists` checks — computed here from
    day_rows rather than via a full analyze(), so the hand-set baseline in
    agg_route_stats isn't clobbered. analyze()'s own builder is covered
    separately by test_analyze_builds_agg_route_daily."""
    from datetime import datetime, time, timezone

    seeded_at = seeded_at or datetime(2026, 6, 9, 10, 0, 0, tzinfo=timezone.utc)
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
        # route_exists reads agg_route_daily; mirror what analyze()
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


async def _seed_live_route(pool, ch_client, agency_id, route_code, service_type, day_rows, history=None):
    """Today's live rows for one route, one poll file per row (day_rows:
    (trip_id, stop_sequence, dep_delay_sec, scheduled_time)). ``history``,
    when given, is the deduped delays of one closed day, seeded into
    agg_route_daily_dist on each of the newest dates of today's class (the
    weekday rule, as no calendar is loaded), enough of them for a same-class
    baseline."""
    from api.range import current_service_date
    from api.routers.map import MIN_SAME_CLASS_BASELINE_DAYS
    from pipeline.clickhouse import LIVE_TABLE, insert_updates
    from tests.fixtures.route_dist import recent_days_of_class, seed_route_dist_day

    today = current_service_date()
    at = _jst_noon_utc(today)
    insert_updates(
        ch_client,
        agency_id,
        [
            (f"live_{route_code}_{service_type}_{i}", at, trip, service_type, sched, route_code, seq, delay)
            for i, (trip, seq, delay, sched) in enumerate(day_rows)
        ],
        table=LIVE_TABLE,
    )
    if history is not None:
        for day in recent_days_of_class(today, MIN_SAME_CLASS_BASELINE_DAYS):
            await seed_route_dist_day(pool, agency_id, route_code, day, history)


@pytest.mark.asyncio
async def test_route_summary_buckets_and_deviation(map_app_ch, ch_client):
    app, agency_id = map_app_ch
    pool = app.state.pool
    # Anomaly: today avg 420s, all blank-service (no scheduled trip matched),
    # against same-class days of 120s each (p90 interpolated to 174s).
    await _seed_live_route(
        pool,
        ch_client,
        agency_id,
        "R_ANOM",
        "",
        [(f"t{i}", 1, 420, "10:00") for i in range(40)],
        history=[120] * 50,
    )
    await _seed_live_route(
        pool,
        ch_client,
        agency_id,
        "R_NOBASE",
        "11_平日(共通)",
        [(f"n{i}", 1, 300, "11:00") for i in range(40)],
    )
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/{agency_id}/today/route-summary")
    assert resp.status_code == 200
    routes = {r["route_code"]: r for r in resp.json()["routes"]}

    anom = routes["R_ANOM"]
    assert anom["bucket"] == "anomaly"
    assert anom["has_baseline"] is True
    assert anom["baseline_avg_sec"] == 120
    assert anom["baseline_p90_sec"] == 174
    assert anom["baseline_samples"] == 150
    assert anom["deviation_sec"] == 300  # 420 - 120
    assert anom["low_confidence"] is False

    nobase = routes["R_NOBASE"]
    assert nobase["bucket"] == "no_baseline"
    assert nobase["has_baseline"] is False
    assert nobase["baseline_avg_sec"] is None
    assert nobase["deviation_sec"] is None


@pytest.mark.asyncio
async def test_route_summary_is_one_row_per_route_across_its_service_ids(map_app_ch, ch_client):
    """Observations matched to two raw service ids, to none (''), or with no
    service at all are one route: one row, its figures pooled, and no raw id
    anywhere in the response."""
    app, agency_id = map_app_ch
    pool = app.state.pool
    for service_type, rows in (
        ("11_平日(共通)", [("a", 1, 60, "10:00"), ("a", 2, 120, "10:05")]),
        ("12_平日(特)", [("b", 1, 180, "11:00")]),
        ("", [("c", 1, 600, "12:00")]),
        (None, [("d", 1, 0, "13:00")]),
    ):
        await _seed_live_route(
            pool, ch_client, agency_id, "R1", service_type, rows, history=[100] * 10 if service_type == "" else None
        )
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/{agency_id}/today/route-summary")
    assert resp.status_code == 200
    [r1] = [r for r in resp.json()["routes"] if r["route_code"] == "R1"]
    assert (r1["samples"], r1["avg_delay_sec"], r1["worst_delay_sec"], r1["trips_observed"]) == (5, 192, 600, 4)
    assert (r1["baseline_avg_sec"], r1["baseline_samples"]) == (100, 30)
    assert "service_type" not in r1
    assert "11_平日(共通)" not in resp.text and "12_平日(特)" not in resp.text


@pytest.mark.asyncio
async def test_route_summary_low_confidence_caps_anomaly(map_app_ch, ch_client):
    app, agency_id = map_app_ch
    pool = app.state.pool
    # Would be anomaly (avg 420 > p90 174) but only 5 obs -> watch + low_confidence
    await _seed_live_route(
        pool,
        ch_client,
        agency_id,
        "R_THIN",
        "",
        [(f"thin{i}", 1, 420, "10:00") for i in range(5)],
        history=[120] * 50,
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
async def test_route_trips_returns_each_trips_ordered_stops(map_app_ch, ch_client):
    """The per-trip `stops` list is what a time-distance diagram draws as one
    polyline, so it must arrive in stop_sequence order with a position on the
    time axis for every stop that has a scheduled time."""
    app, agency_id = map_app_ch
    await _seed_route(
        app.state.pool,
        agency_id,
        "R_MAREY",
        "平日",
        [("A", 1, 600, "08:40"), ("A", 2, 540, "08:50"), ("A", 3, 120, "09:00")],
        ch_client=ch_client,
    )
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/{agency_id}/today/route/R_MAREY/trips")
    body = resp.json()
    assert body["truncated"] is False
    assert body["time_band"] == "all"
    stops = body["trips"][0]["stops"]
    assert [s["stop_sequence"] for s in stops] == [1, 2, 3]
    # scheduled_sec falls back to the clock string: this fixture mirrors only
    # the columns Postgres `updates` has, so ClickHouse's scheduled_sec is NULL.
    assert stops[0]["scheduled_sec"] == 8 * 3600 + 40 * 60
    assert stops[0]["delay_sec"] == 600
    assert stops[0]["observed_sec"] == stops[0]["scheduled_sec"] + 600


@pytest.mark.asyncio
async def test_route_trips_time_band_narrows_to_the_bands_clock_range(map_app_ch, ch_client):
    """?time_band= filters on scheduled_time exactly like every ranged endpoint,
    so the diagram's x window and the rows behind it cannot disagree."""
    app, agency_id = map_app_ch
    await _seed_route(
        app.state.pool,
        agency_id,
        "R_BAND",
        "平日",
        [("EARLY", 1, 600, "08:40"), ("LATE", 1, 300, "13:10")],
        ch_client=ch_client,
    )
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/{agency_id}/today/route/R_BAND/trips?time_band=morning")
    body = resp.json()
    assert body["time_band"] == "morning"
    assert [t["trip_id"] for t in body["trips"]] == ["EARLY"]


@pytest.mark.asyncio
async def test_route_trips_reads_the_requested_jst_day(map_app_ch, ch_client):
    """?date= names the JST calendar day. The fixture's only day answers; the
    day before it is a real, in-window date with nothing observed on it."""
    app, agency_id = map_app_ch
    await _seed_route(
        app.state.pool,
        agency_id,
        "R_DATE",
        "平日",
        [("A", 1, 600, "08:40")],
        ch_client=ch_client,
    )
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        same_day = await client.get(f"/api/{agency_id}/today/route/R_DATE/trips?date=2026-06-09")
        day_before = await client.get(f"/api/{agency_id}/today/route/R_DATE/trips?date=2026-06-08")
    assert same_day.json()["date"] == "2026-06-09"
    assert [t["trip_id"] for t in same_day.json()["trips"]] == ["A"]
    assert day_before.json()["date"] == "2026-06-08"
    assert day_before.json()["trips"] == []


@pytest.mark.asyncio
async def test_route_trips_default_day_is_the_service_day_of_a_post_midnight_observation(map_app_ch, ch_client):
    """A route last seen at 00:40 JST is still running the previous service day,
    so the default day is that day and its trips come back rather than an empty
    page for a calendar day that has no 04:00 start yet."""
    from datetime import datetime, timezone

    app, agency_id = map_app_ch
    await _seed_route(
        app.state.pool,
        agency_id,
        "R_NIGHT",
        "平日",
        [("N1", 1, 120, "23:50")],
        ch_client=ch_client,
        seeded_at=datetime(2026, 6, 9, 15, 40, 0, tzinfo=timezone.utc),  # 00:40 JST on 06-10
    )
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        trips = await client.get(f"/api/{agency_id}/today/route/R_NIGHT/trips")
        profile = await client.get(f"/api/{agency_id}/today/route/R_NIGHT/stop-profile")
    assert trips.json()["date"] == "2026-06-09"
    assert [t["trip_id"] for t in trips.json()["trips"]] == ["N1"]
    assert profile.json()["date"] == "2026-06-09"


@pytest.mark.asyncio
async def test_route_trips_read_a_service_days_after_midnight_tail_from_the_live_table(
    map_app_ch, ch_client, monkeypatch
):
    """At 01:30 JST the service day still running began the calendar day
    before, and its after-midnight rows are only in the live table: promotion
    has not copied the calendar day they fall on, which has not closed."""
    import api.range as range_mod
    import pipeline.clickhouse as clickhouse_mod
    from api.range import current_service_date
    from pipeline.clickhouse import insert_updates
    from tests.fixtures.clock import freeze_now

    app, agency_id = map_app_ch
    # A week ahead of the real clock, so the live table's TTL keeps the row.
    day = current_service_date() + timedelta(days=7)
    next_day = day + timedelta(days=1)
    await _seed_route(app.state.pool, agency_id, "R_TAIL", "平日", [("X", 1, 60, "08:00")])
    insert_updates(
        ch_client,
        agency_id=agency_id,
        rows=[("live_tail.pb", _jst_at(next_day, 0, 30), "TAIL", "平日", "00:25:00", "R_TAIL", 1, 90)],
        table=LIVE_TABLE,
    )
    for module in (clickhouse_mod, range_mod):
        freeze_now(monkeypatch, module, _jst_at(next_day, 1, 30))
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        trips = await client.get(f"/api/{agency_id}/today/route/R_TAIL/trips?date={day.isoformat()}")
    assert [t["trip_id"] for t in trips.json()["trips"]] == ["TAIL"]


@pytest.mark.asyncio
async def test_route_trips_read_an_archive_ingested_previous_day_from_updates(map_app_ch, ch_client, monkeypatch):
    """An agency whose days arrive by archive ingest has yesterday's service
    day in `updates` only, never in the live table."""
    import pipeline.clickhouse as clickhouse_mod
    from tests.fixtures.clock import freeze_now

    app, agency_id = map_app_ch
    await _seed_route(app.state.pool, agency_id, "R_ARCH", "平日", [("ARCH", 1, 60, "08:00")], ch_client=ch_client)
    freeze_now(monkeypatch, clickhouse_mod, datetime(2026, 6, 10, 1, 0, tzinfo=timezone.utc))  # 10:00 JST 06-10
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        trips = await client.get(f"/api/{agency_id}/today/route/R_ARCH/trips?date=2026-06-09")
    assert [t["trip_id"] for t in trips.json()["trips"]] == ["ARCH"]


@pytest.mark.asyncio
async def test_route_trips_read_an_archived_previous_day_over_stray_live_rows(map_app_ch, ch_client):
    """Promotion skips a day archive ingest already filled, leaving that day's
    live polls in the live table until its TTL drops them; the archive in
    `updates` is the whole day, so it wins."""
    from api.range import current_service_date
    from pipeline.clickhouse import insert_updates

    app, agency_id = map_app_ch
    yday = current_service_date() - timedelta(days=1)
    async with app.state.pool.acquire() as conn:
        await _seed_route_existence(conn, agency_id, "R_MIX")
    insert_updates(
        ch_client, agency_id, [("20260101/a.pb", _jst_noon_utc(yday), "ARCH_FULL", "平日", "12:00", "R_MIX", 1, 60)]
    )
    insert_updates(
        ch_client,
        agency_id,
        [("oracle/x/1.pb", _jst_noon_utc(yday), "LIVE_STRAY", "平日", "12:00", "R_MIX", 1, 900)],
        table=LIVE_TABLE,
    )
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        trips = (await client.get(f"/api/{agency_id}/today/route/R_MIX/trips?date={yday.isoformat()}")).json()
    assert [t["trip_id"] for t in trips["trips"]] == ["ARCH_FULL"]


@pytest.mark.asyncio
async def test_route_trips_refuses_a_date_outside_the_scan_window(map_app_ch, ch_client):
    """A date beyond the probe's own bound resolves to the empty response
    rather than an unbounded historical scan of a very large table."""
    app, agency_id = map_app_ch
    await _seed_route(
        app.state.pool,
        agency_id,
        "R_FAR",
        "平日",
        [("A", 1, 600, "08:40")],
        ch_client=ch_client,
    )
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/{agency_id}/today/route/R_FAR/trips?date=2025-01-01")
    assert resp.status_code == 200
    assert resp.json()["date"] is None
    assert resp.json()["trips"] == []


@pytest.mark.asyncio
async def test_route_trips_rejects_a_malformed_date(map_client_ch):
    client, agency_id = map_client_ch
    resp = await client.get(f"/api/{agency_id}/today/route/R/trips?date=not-a-day")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_route_trips_empty_when_no_data(map_client_ch):
    client, agency_id = map_client_ch
    resp = await client.get(f"/api/{agency_id}/today/route/NOPE/trips")
    assert resp.status_code == 200
    body = resp.json()
    body.pop("scope_applied")
    assert body == {"date": None, "time_band": "all", "truncated": False, "trips": []}


@pytest.mark.asyncio
async def test_route_trips_excludes_stale_route_beyond_bound(map_app_ch, ch_client):
    """A route that DOES exist (has an agg_route_daily row, so it passes the
    existence precheck) but whose only ClickHouse observations are older
    than the 30-day bound anchored to the agency's own latest activity must
    resolve to the empty response, not resurrect that stale data as if it
    were "today's". Scanning all history without the bound would return the
    60-day-old trip as current."""
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
    body = resp.json()
    body.pop("scope_applied")
    assert body == {"date": None, "time_band": "all", "truncated": False, "trips": []}


def _jst_noon_utc(day):
    from datetime import datetime, time
    from zoneinfo import ZoneInfo

    return datetime.combine(day, time(12), tzinfo=ZoneInfo("Asia/Tokyo")).astimezone(timezone.utc)


@pytest.mark.asyncio
async def test_route_trips_reads_today_from_updates_live_and_a_past_day_from_updates(
    map_app_ch, ch_client, monkeypatch
):
    """The current service day and the one before read the live table when it
    holds them; the day two back reads `updates`."""
    import api.range as range_mod
    import pipeline.clickhouse as clickhouse_mod
    from api.range import current_service_date
    from pipeline.clickhouse import insert_updates
    from tests.fixtures.clock import freeze_now

    now = datetime.now(timezone.utc)
    for module in (clickhouse_mod, range_mod):
        freeze_now(monkeypatch, module, now)
    app, agency_id = map_app_ch
    today = current_service_date()
    yday = today - timedelta(days=1)
    two_back = today - timedelta(days=2)
    async with app.state.pool.acquire() as conn:
        await _seed_route_existence(conn, agency_id, "R_SPLIT")
    insert_updates(
        ch_client,
        agency_id,
        [
            ("oracle/t/1.pb", _jst_noon_utc(today), "LIVE_TODAY", "平日", "12:00", "R_SPLIT", 1, 300),
            ("oracle/y/1.pb", _jst_noon_utc(yday), "LIVE_YDAY", "平日", "12:00", "R_SPLIT", 1, 900),
            ("oracle/b/1.pb", _jst_noon_utc(two_back), "LIVE_TWO", "平日", "12:00", "R_SPLIT", 1, 900),
        ],
        table=LIVE_TABLE,
    )
    insert_updates(
        ch_client,
        agency_id,
        [
            # A promoted copy of yesterday's live rows, still missing its tail.
            ("oracle/y/0.pb", _jst_noon_utc(yday), "HIST_YDAY", "平日", "12:00", "R_SPLIT", 1, 60),
            ("19990101/b.pb", _jst_noon_utc(two_back), "HIST_TWO", "平日", "12:00", "R_SPLIT", 1, 60),
        ],
    )
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        default = (await client.get(f"/api/{agency_id}/today/route/R_SPLIT/trips")).json()
        yday_trips = (await client.get(f"/api/{agency_id}/today/route/R_SPLIT/trips?date={yday.isoformat()}")).json()
        past = (await client.get(f"/api/{agency_id}/today/route/R_SPLIT/trips?date={two_back.isoformat()}")).json()
    assert default["date"] == today.isoformat()
    assert [t["trip_id"] for t in default["trips"]] == ["LIVE_TODAY"]
    assert [t["trip_id"] for t in yday_trips["trips"]] == ["LIVE_YDAY"]
    assert past["date"] == two_back.isoformat()
    assert [t["trip_id"] for t in past["trips"]] == ["HIST_TWO"]


@pytest.mark.asyncio
async def test_route_stop_profile_empty_when_no_data(map_client_ch):
    """A fabricated/never-observed route_code resolves to the empty response.
    route_stop_profile shares route_trips' existence-precheck + bounded-probe
    logic, so this mirrors test_route_trips_empty_when_no_data to keep both
    copies of that branch covered."""
    client, agency_id = map_client_ch
    resp = await client.get(f"/api/{agency_id}/today/route/NOPE/stop-profile")
    assert resp.status_code == 200
    assert resp.json() == {"date": None, "stops": []}


@pytest.mark.asyncio
async def test_route_stop_profile_excludes_stale_route_beyond_bound(map_app_ch, ch_client):
    """A route that exists (has an agg_route_daily row) but whose only
    ClickHouse observations are older than the 30-day bound anchored to the
    agency's own latest activity must resolve to the empty response,
    mirroring test_route_trips_excludes_stale_route_beyond_bound."""
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
async def test_route_stop_profile_reads_today_from_updates_live(map_app_ch, ch_client):
    from api.range import jst_today
    from pipeline.clickhouse import insert_updates

    app, agency_id = map_app_ch
    today = jst_today()
    async with app.state.pool.acquire() as conn:
        await _seed_route_existence(conn, agency_id, "R_PROF_LIVE")
    insert_updates(
        ch_client,
        agency_id,
        [("oracle/t/1.pb", _jst_noon_utc(today), "A", "平日", "12:00", "R_PROF_LIVE", 1, 300)],
        table=LIVE_TABLE,
    )
    insert_updates(
        ch_client,
        agency_id,
        [("x/1.pb", _jst_noon_utc(today - timedelta(days=1)), "B", "平日", "12:00", "R_PROF_LIVE", 1, 60)],
    )
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        body = (await client.get(f"/api/{agency_id}/today/route/R_PROF_LIVE/stop-profile")).json()
    assert body["date"] == today.isoformat()
    assert [(s["stop_sequence"], s["avg_delay_sec"]) for s in body["stops"]] == [(1, 300)]


@pytest.mark.asyncio
async def test_route_shape_counts_each_trip_day_once(map_app_ch, ch_client):
    """A GTFS trip_id recurs on every service day, so a multi-day range holds
    one observation per trip-day: the trip run on two days gives the stop
    two samples, and its average is over both days, not the latest one."""
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
            "VALUES ($1, 'ST1', '駅前', 40.82, 140.74, ST_SetSRID(ST_MakePoint(140.74, 40.82), 4326))",
            agency_id,
        )
        await conn.execute(
            "INSERT INTO static_stop_times (agency_id, trip_id, stop_sequence, stop_id, arrival_time, departure_time) "
            "VALUES ($1, 'T1', 1, 'ST1', '09:00:00', '09:00:00')",
            agency_id,
        )
        await conn.execute(
            "INSERT INTO updates (agency_id, trip_id, route_code, stop_sequence, dep_delay, captured_at, "
            "file_name, service_type, scheduled_time) "
            "VALUES ($1, 'T1', 'R1', 1, 60, NOW() - INTERVAL '1 day', 'd1.pb', 'weekday', '09:00:00'), "
            "       ($1, 'T1', 'R1', 1, 120, NOW() - INTERVAL '2 days', 'd2.pb', 'weekday', '09:00:00')",
            agency_id,
        )
    from tests.conftest import mirror_updates_to_ch

    mirror_updates_to_ch(ch_client, agency_id)

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/{agency_id}/route-shape?route=R1")

    assert resp.status_code == 200
    [stop] = resp.json()["stops"]
    assert (stop["samples"], stop["avg_min"]) == (2, 1.5)


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
        # captured_at sits on a closed day: the default range is the last 30
        # CLOSED JST days, which excludes today.
        await conn.execute(
            "INSERT INTO updates (agency_id, trip_id, route_code, stop_sequence, dep_delay, captured_at, "
            "file_name, service_type, scheduled_time) "
            "VALUES ($1, 'T1', 'R1', 1, 60, NOW() - INTERVAL '1 day', 'test.pb', 'weekday', '09:00:00'), "
            "       ($1, 'T1', 'R1', 2, 90, NOW() - INTERVAL '1 day', 'test.pb', 'weekday', '09:05:00')",
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
    None. The per-stop stats query must still run: shape_id only PINS stops
    to one variant when multiple exist, never gates whether stats run at
    all -- gating the stats query on chosen_shape_id would silently drop
    `stops` to `[]` for every route on such an agency."""
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
        # captured_at sits on a closed day: the default range is the last 30
        # CLOSED JST days, which excludes today.
        await conn.execute(
            "INSERT INTO updates (agency_id, trip_id, route_code, stop_sequence, dep_delay, captured_at, "
            "file_name, service_type, scheduled_time) "
            "VALUES ($1, 'T1', 'R1', 1, 60, NOW() - INTERVAL '1 day', 'test.pb', 'weekday', '09:00:00'), "
            "       ($1, 'T1', 'R1', 2, 90, NOW() - INTERVAL '1 day', 'test.pb', 'weekday', '09:05:00')",
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


async def _seed_two_way_route(pool, ch_client, agency_id, day):
    """R1 runs two variants: S1 (A→B→C→D→E) and its reverse S2 (D→C→B→A),
    plus S3, which nothing observed. S1's 21 trips read 0/60/120/300 s at
    stops 1-4, five of them 400 s at stop 5; T22 is seen only at stop 3,
    late enough to move stop 3's mean but on no segment. U1 on S2 is seen at
    its first two stops. SX belongs to another route."""
    async with pool.acquire() as conn:
        await _seed_route_existence(conn, agency_id, "R1")
        await conn.execute(
            "INSERT INTO static_stops (agency_id, stop_id, stop_name, stop_lat, stop_lon, geom) "
            "SELECT $1, s.id, s.name, 40.8 + s.k / 100.0, 140.7 + s.k / 100.0, "
            "       ST_SetSRID(ST_MakePoint(140.7 + s.k / 100.0, 40.8 + s.k / 100.0), 4326) "
            "FROM (VALUES ('SA', 'A停', 1), ('SB', 'B停', 2), ('SC', 'C停', 3), ('SD', 'D停', 4), ('SE', 'E停', 5)) "
            "     AS s(id, name, k)",
            agency_id,
        )
        await conn.execute(
            "INSERT INTO static_trips (agency_id, trip_id, route_id, shape_id) "
            "SELECT $1, t.trip_id, t.route_id, t.shape_id FROM ("
            "  SELECT 'T' || n AS trip_id, 'R1' AS route_id, 'S1' AS shape_id FROM generate_series(1, 22) n "
            "  UNION ALL VALUES ('U1', 'R1', 'S2'), ('V1', 'R1', 'S3'), ('X1', 'R2', 'SX')"
            ") AS t",
            agency_id,
        )
        await conn.execute(
            "INSERT INTO static_stop_times (agency_id, trip_id, stop_sequence, stop_id) "
            "SELECT $1, t.trip_id, t.seq, t.stop_id FROM ("
            "  SELECT 'T' || n AS trip_id, s.seq, s.stop_id FROM generate_series(1, 22) n, "
            "         (VALUES (1, 'SA'), (2, 'SB'), (3, 'SC'), (4, 'SD'), (5, 'SE')) AS s(seq, stop_id) "
            "  UNION ALL VALUES ('U1', 1, 'SD'), ('U1', 2, 'SC'), ('U1', 3, 'SB'), ('U1', 4, 'SA'), "
            "                   ('V1', 1, 'SA'), ('V1', 2, 'SC'), ('X1', 1, 'SE')"
            ") AS t",
            agency_id,
        )
        await conn.execute(
            "INSERT INTO updates (agency_id, trip_id, route_code, stop_sequence, dep_delay, captured_at, "
            "file_name, service_type, scheduled_time) "
            "SELECT $1, r.trip_id, 'R1', r.seq, r.delay, $2, 'test.pb', 'weekday', '09:00:00' FROM ("
            "  SELECT 'T' || n AS trip_id, s.seq, s.delay FROM generate_series(1, 21) n, "
            "         (VALUES (1, 0), (2, 60), (3, 120), (4, 300)) AS s(seq, delay) "
            "  UNION ALL SELECT 'T' || n, 5, 400 FROM generate_series(1, 5) n "
            "  UNION ALL VALUES ('T22', 3, 3000), ('U1', 1, 0), ('U1', 2, 30)"
            ") AS r",
            agency_id,
            jst_midday(day),
        )
    from tests.conftest import mirror_updates_to_ch

    mirror_updates_to_ch(ch_client, agency_id)


@pytest.mark.asyncio
async def test_route_shape_lists_each_observed_variant_with_its_share_of_the_route(map_app_ch, ch_client):
    app, agency_id = map_app_ch
    day = jst_today()
    await _seed_two_way_route(app.state.pool, ch_client, agency_id, day)

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/{agency_id}/route-shape?route=R1&from={day}&to={day}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["shape_id"] == "S1"
    assert body["variants"] == [
        {
            "shape_id": "S1",
            "first_stop_name": "A停",
            "last_stop_name": "E停",
            "stop_count": 5,
            "samples": 90,
            "share_pct": 97.8,
        },
        {
            "shape_id": "S2",
            "first_stop_name": "D停",
            "last_stop_name": "A停",
            "stop_count": 4,
            "samples": 2,
            "share_pct": 2.2,
        },
    ]


@pytest.mark.asyncio
async def test_route_shape_measures_each_segment_on_the_trips_seen_at_both_of_its_stops(map_app_ch, ch_client):
    """T22, seen only at stop 3, lifts stop 3's mean but no segment. The
    segment leaving the first stop is not measured, nor is one fewer than
    MIN_GROUP_SAMPLES trips were seen at both ends of (stop 4 to 5)."""
    app, agency_id = map_app_ch
    day = jst_today()
    await _seed_two_way_route(app.state.pool, ch_client, agency_id, day)

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/{agency_id}/route-shape?route=R1&from={day}&to={day}")

    body = resp.json()
    stop3 = next(s for s in body["stops"] if s["stop_sequence"] == 3)
    assert stop3["avg_min"] == 4.18
    assert body["segments"] == [
        {"from_sequence": 2, "to_sequence": 3, "increase_min": 1.0, "pairs": 21},
        {"from_sequence": 3, "to_sequence": 4, "increase_min": 3.0, "pairs": 21},
    ]


@pytest.mark.asyncio
async def test_route_shape_shows_the_variant_asked_for(map_app_ch, ch_client):
    app, agency_id = map_app_ch
    day = jst_today()
    await _seed_two_way_route(app.state.pool, ch_client, agency_id, day)

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/{agency_id}/route-shape?route=R1&shape=S2&from={day}&to={day}")

    body = resp.json()
    assert body["shape_id"] == "S2"
    assert [(s["stop_sequence"], s["stop_name"]) for s in body["stops"]] == [(1, "D停"), (2, "C停")]
    assert [s["stop_sequence"] for s in body["unobserved_stops"]] == [3, 4]
    assert body["segments"] == []
    assert [v["shape_id"] for v in body["variants"]] == ["S1", "S2"]


@pytest.mark.asyncio
async def test_route_shape_shows_a_variant_of_the_route_that_the_window_never_saw(map_app_ch, ch_client):
    """A comparison window pins the variant on screen, observed there or not,
    so its stops line up with the current window's."""
    app, agency_id = map_app_ch
    day = jst_today()
    await _seed_two_way_route(app.state.pool, ch_client, agency_id, day)

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/{agency_id}/route-shape?route=R1&shape=S3&from={day}&to={day}")

    body = resp.json()
    assert body["shape_id"] == "S3"
    assert body["stops"] == []
    assert [s["stop_name"] for s in body["unobserved_stops"]] == ["A停", "C停"]


@pytest.mark.asyncio
async def test_route_shape_ignores_a_variant_of_another_route(map_app_ch, ch_client):
    app, agency_id = map_app_ch
    day = jst_today()
    await _seed_two_way_route(app.state.pool, ch_client, agency_id, day)

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/{agency_id}/route-shape?route=R1&shape=SX&from={day}&to={day}")

    body = resp.json()
    assert body["shape_id"] == "S1"
    assert [s["stop_sequence"] for s in body["stops"]] == [1, 2, 3, 4, 5]


@pytest.mark.asyncio
async def test_route_shape_lists_no_variants_for_a_window_without_observations(map_app_ch, ch_client):
    """The empty-window fallback picks a shape to draw from older raw rows,
    which are no share of anything in the window."""
    app, agency_id = map_app_ch
    day = jst_today()
    await _seed_two_way_route(app.state.pool, ch_client, agency_id, day)
    earlier = day - timedelta(days=60)

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/{agency_id}/route-shape?route=R1&from={earlier}&to={earlier}")

    body = resp.json()
    assert body["shape_id"] == "S1"
    assert body["variants"] == []
    assert body["segments"] == []


@pytest.mark.asyncio
async def test_route_shape_without_shapes_takes_the_first_stop_from_the_timetable(map_app_ch, ch_client):
    """With no shape to list a variant's stops, the timetable of the observed
    trips still names the first stop: stop 1, never observed here, so the
    segment from stop 2 is measured."""
    app, agency_id = map_app_ch
    day = jst_today()
    async with app.state.pool.acquire() as conn:
        await _seed_route_existence(conn, agency_id, "R1")
        await conn.execute(
            "INSERT INTO static_stops (agency_id, stop_id, stop_name, stop_lat, stop_lon, geom) "
            "SELECT $1, 'S' || k, 'Stop' || k, 40.8 + k / 100.0, 140.7 + k / 100.0, "
            "       ST_SetSRID(ST_MakePoint(140.7 + k / 100.0, 40.8 + k / 100.0), 4326) FROM generate_series(1, 4) k",
            agency_id,
        )
        await conn.execute(
            "INSERT INTO static_trips (agency_id, trip_id, route_id, shape_id) "
            "SELECT $1, 'T' || n, 'R1', NULL FROM generate_series(1, 21) n",
            agency_id,
        )
        await conn.execute(
            "INSERT INTO static_stop_times (agency_id, trip_id, stop_sequence, stop_id) "
            "SELECT $1, 'T' || n, k, 'S' || k FROM generate_series(1, 21) n, generate_series(1, 4) k",
            agency_id,
        )
        await conn.execute(
            "INSERT INTO updates (agency_id, trip_id, route_code, stop_sequence, dep_delay, captured_at, "
            "file_name, service_type, scheduled_time) "
            "SELECT $1, 'T' || n, 'R1', s.seq, s.delay, $2, 'test.pb', 'weekday', '09:00:00' "
            "FROM generate_series(1, 21) n, (VALUES (2, 60), (3, 120), (4, 300)) AS s(seq, delay)",
            agency_id,
            jst_midday(day),
        )
    from tests.conftest import mirror_updates_to_ch

    mirror_updates_to_ch(ch_client, agency_id)

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/{agency_id}/route-shape?route=R1&from={day}&to={day}")

    body = resp.json()
    assert body["shape_id"] is None
    assert body["segments"] == [
        {"from_sequence": 2, "to_sequence": 3, "increase_min": 1.0, "pairs": 21},
        {"from_sequence": 3, "to_sequence": 4, "increase_min": 3.0, "pairs": 21},
    ]


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
        await rebuild_stop_clusters(c, agency_id)
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
    """analyze()'s dedup materialization reads ClickHouse, but every test in
    this file seeds Postgres `updates` directly, so mirror the same rows into
    ClickHouse first — see tests.conftest.mirror_updates_to_ch.

    Pins `SET TIME ZONE 'Asia/Tokyo'` on the analyze connection, matching
    every real analyze() caller (gtfs_pipeline._get_conn, the cron endpoint)
    — without it, this connection defaults to UTC, which masks a real defect
    class: bulk-loading ClickHouse's naive-UTC captured_at values straight
    into a timestamptz column is only safe under a UTC session; under the
    JST session production uses, it silently shifts every captured_at (and
    last_seen_at) by 9 hours."""

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
        await rebuild_stop_clusters(c, agency_id)
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
    """analyze() populates agg_route_daily from raw updates."""
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


@pytest.mark.asyncio
async def test_route_summary_freshness_probe_fails_degrades_only_the_header(map_app_ch, ch_client):
    """Both the main per-route query and the informational
    ``latest_captured_at`` freshness probe read ClickHouse, but as two
    separate queries. A hiccup on the freshness probe alone must degrade
    only ``latest_captured_at`` to null, not 500 the whole endpoint — the
    main query's own route rows, also read live from ``updates_live``, must
    still come back."""

    class _FreshnessProbeDown:
        def __init__(self, inner):
            self._inner = inner

        async def query(self, sql, *args, **kwargs):
            if "ORDER BY captured_at DESC LIMIT 1" in sql:
                raise RuntimeError("simulated ClickHouse outage")
            return await self._inner.query(sql, *args, **kwargs)

    app, agency_id = map_app_ch
    pool = app.state.pool
    await _seed_live_route(
        pool,
        ch_client,
        agency_id,
        "R1",
        "平日",
        [(f"t{i}", 1, 300, "10:00") for i in range(25)],
    )
    app.state.ch_client = _FreshnessProbeDown(app.state.ch_client)
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/{agency_id}/today/route-summary")
    assert resp.status_code == 200
    body = resp.json()
    assert body["latest_captured_at"] is None
    r1 = next(r for r in body["routes"] if r["route_code"] == "R1")
    assert r1["avg_delay_sec"] == 300
    assert r1["samples"] == 25


@pytest.mark.asyncio
async def test_route_summary_latest_captured_at_prefers_live_over_history(map_app_ch, ch_client):
    """`latest_captured_at` is the newest `updates_live` poll when the agency
    has one, even though an older `updates` row also exists for it."""
    from pipeline.clickhouse import insert_updates

    app, agency_id = map_app_ch
    now = datetime.now(timezone.utc).replace(microsecond=0)
    live_at = now - timedelta(minutes=1)
    hist_at = now - timedelta(days=2)
    insert_updates(
        ch_client,
        agency_id,
        [("live.pb", live_at, "T1", "平日", "10:00", "R1", 1, 300)],
        table=LIVE_TABLE,
    )
    insert_updates(ch_client, agency_id, [("19990101/h.pb", hist_at, "T2", "平日", "10:00", "R1", 1, 300)])
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/{agency_id}/today/route-summary")
    assert resp.status_code == 200
    assert resp.json()["latest_captured_at"] == live_at.isoformat()


@pytest.mark.asyncio
async def test_route_summary_latest_captured_at_falls_back_to_history_when_live_is_empty(map_app_ch, ch_client):
    """`updates_live` holding nothing for the agency falls back to the newest
    `updates` row rather than leaving the freshness header null."""
    from pipeline.clickhouse import insert_updates

    app, agency_id = map_app_ch
    hist_at = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(days=2)
    insert_updates(ch_client, agency_id, [("19990101/h.pb", hist_at, "T2", "平日", "10:00", "R1", 1, 300)])
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/{agency_id}/today/route-summary")
    assert resp.status_code == 200
    assert resp.json()["latest_captured_at"] == hist_at.isoformat()


@pytest.mark.asyncio
async def test_route_summary_keeps_null_service_routes(map_app, ch_client, ch_async_client):
    """A route whose observations carry no service_type must still surface in
    triage — the live query must not filter them out."""
    app, agency_id = map_app
    app.state.ch_client = ch_async_client
    pool = app.state.pool
    await _seed_live_route(pool, ch_client, agency_id, "R_NULL", None, [("T1", 1, 1680, "10:00")])
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/{agency_id}/today/route-summary")
    assert resp.status_code == 200
    r = next((x for x in resp.json()["routes"] if x["route_code"] == "R_NULL"), None)
    assert r is not None, "NULL-service route was dropped from triage"
    assert "service_type" not in r
    assert r["worst_delay_sec"] == 1680
    assert r["bucket"] == "no_baseline"  # no closed-day history yet
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
    assert body["date"] is None  # analyzed history alone no longer makes a "today"
    assert body["clamp_count"] == 1  # but the 06-07 spike is still surfaced (within 7 days)


@pytest.mark.asyncio
async def test_route_summary_reads_only_todays_live_rows(map_app_ch, ch_client):
    from datetime import timedelta

    from api.range import current_service_date
    from pipeline.clickhouse import LIVE_TABLE, insert_updates

    app, agency_id = map_app_ch
    today = current_service_date()
    yday = today - timedelta(days=1)
    insert_updates(
        ch_client,
        agency_id,
        [
            ("live_new", _jst_noon_utc(today), "T1", "平日", "10:00", "R_NEW", 1, 300),
            ("live_old", _jst_noon_utc(yday), "T2", "平日", "10:00", "R_OLD", 1, 300),
        ],
        table=LIVE_TABLE,
    )
    insert_updates(
        ch_client, agency_id, [("19990101/h.pb", _jst_noon_utc(yday), "T3", "平日", "10:00", "R_HIST", 1, 300)]
    )
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        body = (await client.get(f"/api/{agency_id}/today/route-summary")).json()
    assert body["date"] == today.isoformat()
    assert {r["route_code"] for r in body["routes"]} == {"R_NEW"}


def _future_plain_saturday():
    """A Saturday the weekday rule (no calendar is loaded) files as 土曜, after
    a Friday it files as 平日. A week ahead of the real clock, so
    `updates_live`'s TTL never drops the rows a test captures on it."""
    from api.range import jst_today
    from pipeline.day_types import fallback_day_type

    d = jst_today() + timedelta(days=7)
    while not (fallback_day_type(d) == "saturday" and fallback_day_type(d - timedelta(days=1)) == "weekday"):
        d += timedelta(days=1)
    return d


def _jst_at(day, hour, minute=0, second=0):
    from datetime import time
    from zoneinfo import ZoneInfo

    return datetime.combine(day, time(hour, minute, second), tzinfo=ZoneInfo("Asia/Tokyo")).astimezone(timezone.utc)


def _freeze_clock(monkeypatch, moment):
    """The request's "now" (api.range's clock) is ``moment``."""
    import api.range as range_mod
    from tests.fixtures.clock import freeze_now

    freeze_now(monkeypatch, range_mod, moment)


async def _seed_class_baselines(pool, agency_id, route_code, friday, *, weekday, saturday):
    """Enough closed weekdays before ``friday``, and Saturdays before the day
    after it, for a same-class baseline of either class."""
    from api.routers.map import MIN_SAME_CLASS_BASELINE_DAYS
    from tests.fixtures.route_dist import recent_days_of_class, seed_route_dist_day

    for day in recent_days_of_class(friday, MIN_SAME_CLASS_BASELINE_DAYS):
        await seed_route_dist_day(pool, agency_id, route_code, day, weekday)
    for day in recent_days_of_class(friday + timedelta(days=1), MIN_SAME_CLASS_BASELINE_DAYS):
        await seed_route_dist_day(pool, agency_id, route_code, day, saturday)


@pytest.mark.asyncio
async def test_route_summary_before_the_cutover_is_the_service_day_still_running(map_app_ch, ch_client, monkeypatch):
    """At 01:30 on a Saturday the service day in progress is Friday's: every
    observation since Friday's cutover, its after-midnight tail included,
    judged against Friday's class. The part up to midnight, which promotion
    has already copied into `updates`, counts once, and Friday's own
    aggregate, which analyze writes for that part, is not its baseline."""
    from pipeline.clickhouse import insert_updates
    from tests.fixtures.route_dist import seed_route_dist_day

    app, agency_id = map_app_ch
    pool = app.state.pool
    saturday = _future_plain_saturday()
    friday = saturday - timedelta(days=1)
    _freeze_clock(monkeypatch, _jst_at(saturday, 1, 30))
    friday_evening = ("live_fri_eve", _jst_at(friday, 22), "T2", "平日", "22:00", "R_FRI", 1, 300)
    insert_updates(
        ch_client,
        agency_id,
        [
            ("live_thu_tail", _jst_at(friday, 3, 59, 59), "T0", "平日", "27:59", "R_THU_TAIL", 1, 60),
            ("live_fri_first", _jst_at(friday, 4), "T1", "平日", "04:00", "R_FRI_FIRST", 1, 60),
            friday_evening,
            ("live_fri_tail", _jst_at(saturday, 1), "T3", "平日", "25:00", "R_FRI_TAIL", 1, 120),
        ],
        table=LIVE_TABLE,
    )
    insert_updates(ch_client, agency_id, [friday_evening])
    await _seed_class_baselines(pool, agency_id, "R_FRI", friday, weekday=[100] * 10, saturday=[900] * 10)
    await seed_route_dist_day(pool, agency_id, "R_FRI", friday, [5000] * 10)

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        body = (await client.get(f"/api/{agency_id}/today/route-summary")).json()

    assert body["date"] == friday.isoformat()
    routes = {r["route_code"]: r for r in body["routes"]}
    assert set(routes) == {"R_FRI_FIRST", "R_FRI", "R_FRI_TAIL"}
    assert routes["R_FRI"]["samples"] == 1
    assert (routes["R_FRI"]["baseline_avg_sec"], routes["R_FRI"]["baseline_samples"]) == (100, 30)


@pytest.mark.asyncio
async def test_route_summary_after_the_cutover_is_the_new_service_day(map_app_ch, ch_client, monkeypatch):
    """At 04:30 on a Saturday Saturday's service day has opened: Friday's
    after-midnight tail stays Friday's, and Saturday's class is the baseline."""
    from pipeline.clickhouse import insert_updates

    app, agency_id = map_app_ch
    pool = app.state.pool
    saturday = _future_plain_saturday()
    friday = saturday - timedelta(days=1)
    _freeze_clock(monkeypatch, _jst_at(saturday, 4, 30))
    insert_updates(
        ch_client,
        agency_id,
        [
            ("live_fri_eve", _jst_at(friday, 22), "T1", "平日", "22:00", "R_FRI", 1, 300),
            ("live_fri_tail", _jst_at(saturday, 3, 59, 59), "T2", "平日", "27:59", "R_FRI_TAIL", 1, 120),
            ("live_sat_first", _jst_at(saturday, 4), "T3", "土曜", "04:00", "R_SAT", 1, 600),
        ],
        table=LIVE_TABLE,
    )
    await _seed_class_baselines(pool, agency_id, "R_SAT", friday, weekday=[100] * 10, saturday=[900] * 10)

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        body = (await client.get(f"/api/{agency_id}/today/route-summary")).json()

    assert body["date"] == saturday.isoformat()
    routes = {r["route_code"]: r for r in body["routes"]}
    assert set(routes) == {"R_SAT"}
    assert (routes["R_SAT"]["baseline_avg_sec"], routes["R_SAT"]["baseline_samples"]) == (900, 30)


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


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/api/{a}/route-shape?route=R1", "/api/{a}/today/route/R1/trips"])
async def test_clickhouse_screen_endpoints_declare_scope_applied(map_client_ch, path):
    from api.scope_applied import SCOPE_FIELDS

    client, agency_id = map_client_ch
    resp = await client.get(path.format(a=agency_id))
    assert resp.status_code == 200, resp.text
    applied = resp.json()["scope_applied"]
    assert set(applied) == set(SCOPE_FIELDS)
    assert applied["hour"] is False
