import os
from datetime import date, datetime, timedelta, timezone

import asyncpg
import pytest

from api.range import RangeCtx
from pipeline.query.tools import _is_route_registered, dispatch

DATABASE_URL = os.environ["DATABASE_URL"]


def _analyze_sync(agency_id, ch_client):
    """Build the agg_* tables (JST-pinned, like the real pipeline) so the
    aggregate-backed tool paths have data.

    analyze()'s dedup materialization now reads ClickHouse (Task 6); the
    fixture below seeds Postgres `updates` directly (pre-dating that
    migration), so mirror the same rows into ClickHouse first — see
    tests.conftest.mirror_updates_to_ch."""
    import psycopg2

    from pipeline.analyze import analyze
    from tests.conftest import mirror_updates_to_ch

    mirror_updates_to_ch(ch_client, agency_id)
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("SET TIME ZONE 'Asia/Tokyo'")
    conn.autocommit = False
    try:
        analyze(agency_id, conn, ch_client)
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
async def conn_routes(apply_schema):
    pool = await asyncpg.create_pool(DATABASE_URL)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO agencies (agency_name, feed_url) VALUES ('T', 'http://t') RETURNING agency_id"
        )
        agency_id = row["agency_id"]
        await conn.execute(
            "INSERT INTO static_routes (agency_id, route_id, route_short_name) "
            "VALUES ($1, '国道線(1021)', 'A1 国道・古川線')",
            agency_id,
        )
    yield pool, agency_id
    async with pool.acquire() as c:
        await c.execute("TRUNCATE agencies CASCADE")
    await pool.close()


def _ctx():
    return RangeCtx(from_date=date(2026, 5, 1), to_date=date(2026, 5, 26))


@pytest.mark.asyncio
async def test_dispatch_describe_data_routes(conn_routes):
    pool, agency_id = conn_routes
    async with pool.acquire() as conn:
        result = await dispatch("describe_data", {"kind": "routes"}, _ctx(), conn, agency_id, locale="ja")
    assert result.kind == "table"
    assert any(row[0] == "1021" for row in result.rows)


@pytest.mark.asyncio
async def test_dispatch_route_alias_resolution(conn_routes):
    """route_stats called with 'A1' should resolve to 1021 before SQL hits."""
    pool, agency_id = conn_routes
    async with pool.acquire() as conn:
        result = await dispatch("route_stats", {"route": "A1"}, _ctx(), conn, agency_id, locale="ja")
    # No observations seeded → empty result, but the 'not registered' message
    # must NOT appear since A1 resolved to a real route_code.
    assert "登録されている系統コード" not in result.summary


@pytest.mark.asyncio
async def test_dispatch_route_unresolved_returns_candidates(conn_routes):
    pool, agency_id = conn_routes
    async with pool.acquire() as conn:
        result = await dispatch("route_stats", {"route": "中心部"}, _ctx(), conn, agency_id, locale="ja")
    assert result.kind == "empty"
    # Either a "もしかして" suggestion or the original "not registered" message
    # is acceptable, as long as it's not a hallucinated SQL run.
    assert "もしかして" in result.summary or "見つかりません" in result.summary or "登録" in result.summary


@pytest.fixture
async def conn_routes_with_alias(apply_schema):
    """Seed routes whose names trigger a trigram match for a deliberately
    similar input — used to assert the 'did you mean' message localises."""
    pool = await asyncpg.create_pool(DATABASE_URL)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO agencies (agency_name, feed_url) VALUES ('T', 'http://t') RETURNING agency_id"
        )
        agency_id = row["agency_id"]
        await conn.executemany(
            "INSERT INTO static_routes (agency_id, route_id, route_short_name, route_long_name) "
            "VALUES ($1, $2, $3, $4)",
            [
                (agency_id, "中央大橋線(12211)", "L21 中央大橋線", None),
                (agency_id, "中央大橋線(16021)", "L31 中央大橋線", None),
                (agency_id, "中央大橋線(17091)", "L30 中央大橋線", None),
            ],
        )
    yield pool, agency_id
    async with pool.acquire() as c:
        await c.execute("TRUNCATE agencies CASCADE")
    await pool.close()


@pytest.mark.asyncio
async def test_dispatch_did_you_mean_locale_en(conn_routes_with_alias):
    """When the route resolver returns candidates (no confident match), the
    'did you mean' summary must render in English for locale='en'."""
    pool, agency_id = conn_routes_with_alias
    async with pool.acquire() as conn:
        result = await dispatch("route_stats", {"route": "中央大橋線"}, _ctx(), conn, agency_id, locale="en")
    assert result.kind == "empty"
    # English message: "not found. Did you mean: ..."
    assert "not found" in result.summary
    assert "Did you mean" in result.summary
    # JP phrasing must not leak through.
    assert "もしかして" not in result.summary


@pytest.mark.asyncio
async def test_dispatch_capabilities(conn_routes):
    pool, agency_id = conn_routes
    async with pool.acquire() as conn:
        result = await dispatch("capabilities", {}, _ctx(), conn, agency_id, locale="ja")
    assert result.kind == "kv"
    cats = {k for k, _ in result.pairs}
    assert "meta" in cats


@pytest.fixture
async def conn_two_routes_obs(apply_schema, ch_client):
    """Two routes, only route A has observations — used to assert that the
    time_series tool applies the route filter from args."""
    pool = await asyncpg.create_pool(DATABASE_URL)
    async with pool.acquire() as conn:
        await conn.execute("SET TIME ZONE 'Asia/Tokyo'")
        row = await conn.fetchrow(
            "INSERT INTO agencies (agency_name, feed_url) VALUES ('T', 'http://t') RETURNING agency_id"
        )
        agency_id = row["agency_id"]
        await conn.executemany(
            "INSERT INTO static_routes (agency_id, route_id, route_short_name) VALUES ($1, $2, $3)",
            [
                (agency_id, "国道線(1021)", "A1 国道・古川線"),
                (agency_id, "新町線(3021)", "B1 新町線"),
            ],
        )
        # 30 observations for route 1021 only (6/day across 5 days so the
        # HAVING COUNT(*) > 5 gate in compute_trend_series is satisfied);
        # route 3021 has none.
        await conn.executemany(
            "INSERT INTO updates "
            "(agency_id, file_name, trip_id, route_code, stop_sequence, captured_at, "
            " scheduled_time, service_type, dep_delay) "
            "VALUES ($1, $2, $3, $4, $5, $6, '08:00'::time, '平日', 60)",
            [
                (
                    agency_id,
                    f"pb_{d}_{s}",
                    f"T{d}_{s}",
                    "1021",
                    s,
                    datetime(2026, 5, d + 1, 8, 0, 0),
                )
                for d in range(5)
                for s in range(1, 7)  # 6 stop_sequences per day → 6 dedup-unique rows
            ],
        )
    # time_series → compute_trend_series reads agg_daily_trend; build it.
    _analyze_sync(agency_id, ch_client)
    yield pool, agency_id
    async with pool.acquire() as c:
        await c.execute("TRUNCATE agencies CASCADE")
    await pool.close()


@pytest.mark.asyncio
async def test_dispatch_time_series_applies_route_filter(conn_two_routes_obs):
    """time_series called with a route arg must narrow compute_trend_series to
    only that route's observations (regression for missing ctx.routes push)."""
    pool, agency_id = conn_two_routes_obs
    async with pool.acquire() as conn:
        # First: series without a route arg returns all-route sample count.
        all_result = await dispatch("time_series", {}, _ctx(), conn, agency_id, locale="ja")
        # Then: series scoped to route 1021 (which is the only one with data).
        route_result = await dispatch("time_series", {"route": "1021"}, _ctx(), conn, agency_id, locale="ja")
    # Same data in both (only one route has data), but the route-filtered
    # call must have actually applied the filter — we verify by also calling
    # for route 3021 (no data) and asserting an empty result.
    assert all_result.kind == "series"
    assert route_result.kind == "series"
    # Both the unfiltered series and the route=1021 series see the same
    # data (only 1021 has observations), so their sample totals must match.
    all_samples = sum(d.get("samples", 0) for d in all_result.series)
    route_samples = sum(d.get("samples", 0) for d in route_result.series)
    assert all_samples > 0
    assert route_samples == all_samples

    async with pool.acquire() as conn:
        empty_result = await dispatch("time_series", {"route": "3021"}, _ctx(), conn, agency_id, locale="ja")
    # Route 3021 has zero observations → empty series. If the route filter
    # were NOT applied this would return the all_result data instead.
    assert empty_result.kind == "empty"


class _ExplodingChClient:
    """Stand-in ``ch`` that fails the test if `_is_route_registered` ever
    reaches the ClickHouse fallback query — used to prove the agg_route_daily
    fast path resolves True on its own, without ever touching ClickHouse."""

    async def query(self, *args, **kwargs):
        raise AssertionError("agg_route_daily fast path should have short-circuited before any ClickHouse query")


@pytest.mark.asyncio
async def test_is_route_registered_fast_path_hits_agg_route_daily_not_clickhouse(aconn, aagency_id):
    """A route already recorded in agg_route_daily (i.e. analyze() has run
    at least once for it) must resolve True from the fast Postgres check
    alone. Demoting the ClickHouse scan to a fallback (rather than removing
    it) must not reintroduce it on the common, already-analyzed-route path —
    proven here via a `ch` stub that raises if queried at all."""
    await aconn.execute(
        "INSERT INTO agg_route_daily "
        "(agency_id, date, route_code, service_type, avg_delay_sec, worst_delay_sec, "
        " trips_observed, samples, last_seen_at) "
        "VALUES ($1, CURRENT_DATE, 'FASTPATH', '平日', 60, 120, 3, 10, now())",
        aagency_id,
    )
    result = await _is_route_registered("FASTPATH", aconn, aagency_id, ch=_ExplodingChClient())
    assert result is True


@pytest.mark.asyncio
async def test_is_route_registered_falls_back_to_clickhouse_when_agg_stale(
    aconn, aagency_id, ch_client, ch_async_client
):
    """A route with live observations already in ClickHouse but nothing yet
    in agg_route_daily (e.g. a brand-new agency/route whose analyze() run
    hasn't happened yet, or failed — api/routers/internal.py's cron loop
    deliberately tolerates per-agency analyze() failures) must still resolve
    True via the ClickHouse fallback rather than a false 'not registered'."""
    from pipeline.clickhouse import insert_updates

    insert_updates(
        ch_client,
        aagency_id,
        [
            (
                "raw_fallback.pb",
                datetime.now(timezone.utc),
                "trip_raw",
                "平日",
                "10:00:00",
                "RAWFALLBACK",
                1,
                60,
            )
        ],
    )
    result = await _is_route_registered("RAWFALLBACK", aconn, aagency_id, ch=ch_async_client)
    assert result is True


@pytest.mark.asyncio
async def test_is_route_registered_no_horizon_scans_unbounded_not_fixed_30_days(
    aconn, aagency_id, ch_client, ch_async_client
):
    """Regression: when an agency has ZERO agg_route_daily rows at all (the
    normal state right after a bulk historical backfill, before analyze()
    has ever completed -- not a rare corner), there is no analyze horizon to
    bound against. A fixed 30-day-off-max_captured_at bound in that state
    reintroduces the exact false-negative this function exists to prevent:
    a route observed only far in the past (here, 60 days before the
    agency's latest ClickHouse data) must still resolve True."""
    from pipeline.clickhouse import insert_updates

    now = datetime.now(timezone.utc)
    insert_updates(
        ch_client,
        aagency_id,
        [("old.pb", now - timedelta(days=60), "trip_old", "平日", "10:00:00", "OLDROUTE", 1, 60)],
    )
    # Unrelated recent traffic so the agency's overall latest captured_at is
    # today, not day-60 -- this is what made the old fixed-30-day bound
    # (today - 30d) exclude OLDROUTE's day-60 observation.
    insert_updates(
        ch_client,
        aagency_id,
        [("today.pb", now, "trip_today", "平日", "11:00:00", "UNRELATED", 1, 30)],
    )

    result = await _is_route_registered("OLDROUTE", aconn, aagency_id, ch=ch_async_client)
    assert result is True


@pytest.mark.asyncio
async def test_is_route_registered_returns_false_when_absent_everywhere(aconn, aagency_id, ch_client, ch_async_client):
    """A route absent from static_routes, agg_route_daily, AND raw ClickHouse
    `updates` is genuinely unregistered → False."""
    result = await _is_route_registered("NEVER_SEEN", aconn, aagency_id, ch=ch_async_client)
    assert result is False


@pytest.mark.asyncio
async def test_is_route_registered_uses_analyze_horizon_not_a_fixed_window(
    aconn, aagency_id, ch_client, ch_async_client
):
    """Regression: the ClickHouse fallback's bound must reflect how far
    behind analyze() actually is for this agency, not a fixed 30-day window
    off ClickHouse's own latest data.

    Scenario: analyze() has been stuck for 60 days (agg_route_daily's
    horizon for this agency is 60 days old — some OTHER route's data), but
    ingest_live kept working, so a route observed 45 days ago (well past
    analyze's horizon, so genuinely "not yet analyzed") already sits in
    ClickHouse. ClickHouse's overall agency-latest captured_at is recent
    (today, from other traffic) — a fixed `agency_latest - 30 days` bound
    would put the cutoff at day-30, excluding LAGROUTE's day-45 observation
    and reporting a route analyze() simply hasn't caught up to as
    unregistered. Bounding by the analyze horizon instead (day-60) correctly
    includes it.
    """
    from pipeline.clickhouse import insert_updates

    now = datetime.now(timezone.utc)
    horizon_date = (now - timedelta(days=60)).date()
    lag_observed_at = now - timedelta(days=45)

    # Establishes agg_route_daily's horizon at day-60 (some other, already-
    # analyzed route) -- simulates analyze() being stuck there.
    await aconn.execute(
        "INSERT INTO agg_route_daily (agency_id, date, route_code, service_type, "
        "avg_delay_sec, worst_delay_sec, trips_observed, samples, last_seen_at) "
        "VALUES ($1, $2, 'OTHERROUTE', 'weekday', 30, 60, 1, 5, $3)",
        aagency_id,
        horizon_date,
        now,
    )
    # LAGROUTE: real ClickHouse data at day-45 -- inside the analyze horizon
    # window (after day-60), so genuinely "ingested but not yet analyzed".
    insert_updates(
        ch_client,
        aagency_id,
        [("lag.pb", lag_observed_at, "trip_lag", "平日", "10:00:00", "LAGROUTE", 1, 60)],
    )
    # Unrelated recent ClickHouse traffic (a different route) so the
    # agency's overall latest captured_at is today, not day-45 -- this is
    # what makes the fixed `agency_latest - 30d` bound (day-30) diverge from
    # the horizon-based bound (day-60) in this test.
    insert_updates(
        ch_client,
        aagency_id,
        [("today.pb", now, "trip_today", "平日", "11:00:00", "UNRELATED", 1, 30)],
    )

    result = await _is_route_registered("LAGROUTE", aconn, aagency_id, ch=ch_async_client)
    assert result is True


@pytest.mark.asyncio
async def test_dispatch_segment_hotspots_returns_table(aconn, aagency_id, ch_client, ch_async_client):
    """dispatch('segment_hotspots', ...) with live ClickHouse data for a
    registered-by-observation route must return a table with the worst
    stop_sequence(s) by average delay (Task 1: WHERE delay accumulates)."""
    now = datetime.now(timezone.utc)
    for i in range(6):
        await aconn.execute(
            "INSERT INTO updates "
            "(agency_id, file_name, captured_at, trip_id, service_type, "
            " scheduled_time, route_code, stop_sequence, dep_delay) "
            "VALUES ($1, $2, $3, $4, '平日', '10:00', 'R1', 2, $5)",
            aagency_id,
            f"pb_hot_{i}",
            now + timedelta(minutes=i),
            f"trip_hot_{i}",
            300,  # 5 min
        )
    # Real static-schedule data so the assertion below exercises the actual
    # Postgres stop-name-resolution join, not just its fallback -- see the
    # matching comment in test_tool_queries.py's
    # test_segment_hotspots_ranks_stops_by_avg_delay for why every seeded
    # trip (not just one) gets a static_stop_times row.
    await aconn.execute(
        "INSERT INTO static_stops (agency_id, stop_id, stop_name) VALUES ($1, 'stop_hot', 'テスト停留所')",
        aagency_id,
    )
    await aconn.executemany(
        "INSERT INTO static_stop_times (agency_id, trip_id, stop_sequence, stop_id) VALUES ($1, $2, 2, 'stop_hot')",
        [(aagency_id, f"trip_hot_{i}") for i in range(6)],
    )
    from tests.conftest import mirror_updates_to_ch

    mirror_updates_to_ch(ch_client, aagency_id)
    ctx = RangeCtx(from_date=(now.date() - timedelta(days=1)), to_date=(now.date() + timedelta(days=1)))
    result = await dispatch(
        "segment_hotspots", {"route": "R1"}, ctx, aconn, aagency_id, locale="ja", ch=ch_async_client
    )
    assert result.kind == "table"
    assert result.columns == ["stop_sequence", "stop_name", "avg_min", "samples"]
    assert result.rows[0][0] == 2
    assert result.rows[0][1] == "テスト停留所"
    assert result.rows[0][2] == 5.0
    assert result.rows[0][3] == 6


@pytest.mark.asyncio
async def test_dispatch_schedule_realism_returns_table(aconn, aagency_id, ch_client, ch_async_client):
    """dispatch('schedule_realism', ...) with live ClickHouse data for a
    registered-by-observation route must return a table flagging the
    stop-to-stop segment where delay is systematically ADDED (Task 3: is
    the timetable itself too tight)."""
    now = datetime.now(timezone.utc)
    for i in range(6):
        trip = f"trip_grow_{i}"
        await aconn.execute(
            "INSERT INTO updates "
            "(agency_id, file_name, captured_at, trip_id, service_type, "
            " scheduled_time, route_code, stop_sequence, dep_delay) "
            "VALUES "
            "($1, $2, $3, $4, '平日', '10:00', 'R1', 1, 10), "
            "($1, $5, $6, $4, '平日', '10:05', 'R1', 2, 310)",
            aagency_id,
            f"pb_grow_a_{i}",
            now + timedelta(minutes=i),
            trip,
            f"pb_grow_b_{i}",
            now + timedelta(minutes=i, seconds=30),
        )
    from tests.conftest import mirror_updates_to_ch

    mirror_updates_to_ch(ch_client, aagency_id)
    ctx = RangeCtx(from_date=(now.date() - timedelta(days=1)), to_date=(now.date() + timedelta(days=1)))
    result = await dispatch(
        "schedule_realism", {"route": "R1"}, ctx, aconn, aagency_id, locale="ja", ch=ch_async_client
    )
    assert result.kind == "table"
    assert result.columns == ["stop_sequence", "next_stop_sequence", "avg_added_min", "samples"]
    assert result.rows[0][0] == 1
    assert result.rows[0][1] == 2
    assert result.rows[0][2] == pytest.approx(5.0, abs=0.01)
    assert result.rows[0][3] == 6


@pytest.mark.asyncio
async def test_dispatch_time_pattern_returns_table(aconn, aagency_id):
    """dispatch('time_pattern', ...) reads agg_route_hour_dow directly (pure
    Postgres, no ClickHouse involved) and must sort the worst hour x
    day-of-week combination first (Task 2: WHEN delay is worst).

    Registers R1 via agg_route_daily (the _is_route_registered fast path)
    rather than seeding static_routes, since this tool's own data source
    (agg_route_hour_dow) is otherwise unrelated to route registration.
    An unrelated route (R2) with only 3 samples per cell (below the
    ``HAVING SUM(samples) > 5`` gate) must not appear in the result.
    """
    await aconn.execute(
        "INSERT INTO agg_route_daily "
        "(agency_id, date, route_code, service_type, avg_delay_sec, worst_delay_sec, "
        " trips_observed, samples, last_seen_at) "
        "VALUES ($1, CURRENT_DATE, 'R1', '平日', 60, 120, 3, 10, now())",
        aagency_id,
    )
    await aconn.execute(
        "INSERT INTO agg_route_hour_dow (agency_id, route_code, service_type, dow, hour, avg_min, samples) "
        "VALUES "
        "  ($1, 'R1', '平日', 1, 8, 2.0, 20),"
        "  ($1, 'R1', '平日', 5, 18, 6.5, 40),"
        "  ($1, 'R1', '平日', 3, 9, 1.0, 3)",  # below the samples > 5 gate
        aagency_id,
    )
    result = await dispatch("time_pattern", {"route": "R1"}, _ctx(), aconn, aagency_id, locale="ja")
    assert result.kind == "table"
    assert result.columns == ["dow", "hour", "avg_min", "samples"]
    # Worst hour/dow (Fri 18:00, avg 6.5 min) must sort first.
    assert result.rows[0][1] == 18
    assert result.rows[0][2] == 6.5
    assert result.rows[0][3] == 40
    # Only the two cells that clear the samples > 5 gate are returned.
    assert len(result.rows) == 2
    hours = {row[1] for row in result.rows}
    assert hours == {8, 18}


@pytest.mark.asyncio
async def test_dispatch_trend_shift_returns_kv(aconn, aagency_id):
    """dispatch('trend_shift', ...) reads agg_daily_trend directly (pure
    Postgres, no ClickHouse involved, same as time_pattern) and must report
    a large positive delta_min for a route whose delay jumps partway
    through the window (Task 4: chronic pattern vs regime shift).

    Registers R1 via agg_route_daily (the _is_route_registered fast path),
    same convention as test_dispatch_time_pattern_returns_table.
    """
    today = date.today()
    days = [today - timedelta(days=3), today - timedelta(days=2), today - timedelta(days=1), today]
    avgs = [1.0, 1.2, 5.0, 5.5]
    await aconn.execute(
        "INSERT INTO agg_route_daily "
        "(agency_id, date, route_code, service_type, avg_delay_sec, worst_delay_sec, "
        " trips_observed, samples, last_seen_at) "
        "VALUES ($1, CURRENT_DATE, 'R1', '平日', 60, 120, 3, 10, now())",
        aagency_id,
    )
    for d, avg in zip(days, avgs, strict=True):
        await aconn.execute(
            "INSERT INTO agg_daily_trend (agency_id, date, route_code, service_type, avg_min, samples) "
            "VALUES ($1, $2, 'R1', '平日', $3, 20)",
            aagency_id,
            d.isoformat(),
            avg,
        )
    ctx = RangeCtx(from_date=days[0], to_date=days[-1])
    result = await dispatch("trend_shift", {"route": "R1"}, ctx, aconn, aagency_id, locale="ja")
    assert result.kind == "kv"
    labels = [p[0] for p in result.pairs]
    values = [p[1] for p in result.pairs]
    assert labels == ["前半平均", "後半平均", "変化幅"]
    assert float(values[0]) < float(values[1])
    assert float(values[2]) == pytest.approx(4.15, abs=0.1)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool_name",
    ["route_stats", "segment_hotspots", "time_pattern", "schedule_realism", "trend_shift"],
)
async def test_dispatch_missing_route_arg_returns_empty(aconn, aagency_id, tool_name):
    """Route-required tools must short-circuit with the 'route_arg_required'
    message (not attempt registration/data lookup) when no route arg is
    given. Characterization test pinning this branch ahead of slice 2's
    ``_resolve_and_check_route`` guard consolidation (docs/refactor-plan.md)."""
    ctx = RangeCtx(from_date=date(2026, 5, 1), to_date=date(2026, 5, 26))
    result = await dispatch(tool_name, {}, ctx, aconn, aagency_id, locale="ja")
    assert result.kind == "empty"
    assert result.summary == "route 引数が必要です。"


@pytest.mark.asyncio
async def test_dispatch_missing_route_arg_en_locale(aconn, aagency_id):
    ctx = RangeCtx(from_date=date(2026, 5, 1), to_date=date(2026, 5, 26))
    result = await dispatch("segment_hotspots", {}, ctx, aconn, aagency_id, locale="en")
    assert result.kind == "empty"
    assert result.summary == "The route argument is required."
