"""Cross-agency network summary — compute + endpoint (transit_test only)."""

from datetime import date, datetime, time, timezone

import httpx
import pytest
from httpx import ASGITransport

from pipeline.reports.network import compute_network_summary
from tests.conftest import _test_pool, confirm_rt_field_coverage

_TRUNCATE_SQL = (
    "TRUNCATE agencies, agg_route_daily_dist, agg_feed_health, agg_service_delivered_daily, "
    "ridership_weights, updates CASCADE"
)


@pytest.fixture
async def net_pool(apply_schema):
    # In-process compute cache is keyed on (from_date, to_date) only, so two
    # tests sharing a date range would leak results — clear it per test.
    compute_network_summary.cache_clear()
    pool = await _test_pool()
    async with pool.acquire() as c:
        await c.execute(_TRUNCATE_SQL)
        ins = "INSERT INTO agencies (agency_name, feed_url) VALUES ($1,$2) RETURNING agency_id"
        a = await c.fetchrow(ins, "A", "http://na")
        b = await c.fetchrow(ins, "B", "http://nb")
        cc = await c.fetchrow(ins, "C", "http://nc")
    await _trust_service_delivered(pool, a["agency_id"], b["agency_id"], cc["agency_id"])
    yield pool, a["agency_id"], b["agency_id"], cc["agency_id"]
    async with pool.acquire() as c:
        await c.execute(_TRUNCATE_SQL)
    await pool.close()


async def _seed_route_dist(pool, aid, route_code, dist):
    """dist: list of (date_iso, samples, sum_delay_sec, on_time_count) for one route_code
    (unlike _seed below, which hardcodes route_code='R1' -- this lets a test seed
    several distinct routes under one agency, needed for ridership-weighting tests)."""
    async with pool.acquire() as c:
        for d, n, sd, ot in dist:
            await c.execute(
                "INSERT INTO agg_route_daily_dist (agency_id, date, route_code, service_type, "
                "samples, sum_delay_sec, on_time_count, late5_count, hist) "
                "VALUES ($1,$2,$3,'平日',$4,$5,$6,0,$7)",
                aid,
                date.fromisoformat(d),
                route_code,
                n,
                sd,
                ot,
                [0] * 37,
            )


async def _seed_ridership_weight(pool, aid, route_code, weight):
    """route_code=None inserts that agency's default (route_code IS NULL) weight row."""
    async with pool.acquire() as c:
        await c.execute(
            "INSERT INTO ridership_weights (agency_id, route_code, weight) VALUES ($1,$2,$3)",
            aid,
            route_code,
            weight,
        )


async def _seed(pool, aid, *, dist, feed=None, updates_at=None):
    """dist: list of (date_iso, samples, sum_delay_sec, on_time_count). feed: (date_iso, raw, clamp)."""
    async with pool.acquire() as c:
        for d, n, sd, ot in dist:
            await c.execute(
                "INSERT INTO agg_route_daily_dist (agency_id, date, route_code, service_type, "
                "samples, sum_delay_sec, on_time_count, late5_count, hist) "
                "VALUES ($1,$2,'R1','平日',$3,$4,$5,0,$6)",
                aid,
                date.fromisoformat(d),
                n,
                sd,
                ot,
                [0] * 37,
            )
        if feed:
            d, raw, clamp = feed
            await c.execute(
                "INSERT INTO agg_feed_health (agency_id, date, raw_samples, clamp_count) VALUES ($1,$2,$3,$4)",
                aid,
                date.fromisoformat(d),
                raw,
                clamp,
            )
        if updates_at:
            await c.execute(
                "INSERT INTO updates (agency_id, file_name, captured_at, trip_id, service_type, "
                "scheduled_time, route_code, stop_sequence, dep_delay) "
                "VALUES ($1,'f.pb',$2,'T1','平日',$3,'R1',1,60)",
                aid,
                updates_at,
                time(11, 37),
            )


async def _seed_static_schedule(pool, aid, *, service_id: str, trip_ids: list[str], svc_date: str) -> None:
    """Seed one static-GTFS "planned trips" fixture: `len(trip_ids)` trips
    under `service_id`, all scheduled to run on `svc_date` (YYYYMMDD text,
    matching calendar_dates.txt's raw format) via exception_type=1.
    """
    async with pool.acquire() as c:
        await c.execute(
            "INSERT INTO static_calendar_dates (agency_id, service_id, date, exception_type) VALUES ($1,$2,$3,1)",
            aid,
            service_id,
            svc_date,
        )
        for tid in trip_ids:
            await c.execute(
                "INSERT INTO static_trips (agency_id, trip_id, route_id, service_id) VALUES ($1,$2,'R1',$3)",
                aid,
                tid,
                service_id,
            )


async def _set_ingest_strategy(pool, aid, strategy):
    async with pool.acquire() as c:
        await c.execute("UPDATE agencies SET ingest_strategy = $1 WHERE agency_id = $2", strategy, aid)


async def _trust_service_delivered(pool, *agency_ids):
    """Simulate `scripts/probe_rt_field_coverage.py --record` having already
    confirmed real (non-empty) RT field coverage for these dynamically-created
    test agencies -- production only trusts an agency with a live verdict in
    `rt_field_coverage_probes`, which a freshly-inserted test agency has no
    rows in. Leaves `ingest_strategy` alone: that is the gate's other,
    independent half."""
    await confirm_rt_field_coverage(pool, *agency_ids)


async def _seed_static_version_summary(pool, aid, version, trip_count, vehicle_km=None, computed_at=None):
    """Seed one `agg_static_version_summary` row directly (bypassing
    `pipeline.analyze.analyze()`, same "seed the precomputed aggregate
    directly" convention as `_seed_service_delivered_daily` below) --
    `computed_at` lets a test control which of several rows for one agency
    is "most recently computed" (the read path's tie-break)."""
    async with pool.acquire() as c:
        await c.execute(
            "INSERT INTO agg_static_version_summary "
            "(agency_id, static_version_id, trip_count, vehicle_km, computed_at) "
            "VALUES ($1,$2,$3,$4,COALESCE($5, now()))",
            aid,
            version,
            trip_count,
            vehicle_km,
            computed_at,
        )


async def _seed_service_delivered_daily(pool, aid, rows):
    """rows: list of (date_iso, non_executed_trips)."""
    async with pool.acquire() as c:
        for d, n in rows:
            await c.execute(
                "INSERT INTO agg_service_delivered_daily (agency_id, date, non_executed_trips) VALUES ($1,$2,$3)",
                aid,
                date.fromisoformat(d),
                n,
            )


async def _seed_saved_plan(pool, aid, rows):
    """rows: list of (date_iso, planned_trips) saved in planned_trips_daily."""
    async with pool.acquire() as c:
        for d, n in rows:
            await c.execute(
                "INSERT INTO planned_trips_daily (agency_id, service_date, planned_trips) VALUES ($1,$2,$3)",
                aid,
                date.fromisoformat(d),
                n,
            )


async def _seed_observed_trips(pool, aid, rows):
    """rows: list of (date_iso, trips_observed) as agg_route_daily rows on route R1."""
    async with pool.acquire() as c:
        for d, trips in rows:
            await c.execute(
                "INSERT INTO agg_route_daily (agency_id, date, route_code, service_type, avg_delay_sec, "
                "worst_delay_sec, trips_observed, samples, last_seen_at, sum_delay_sec) "
                "VALUES ($1,$2,'R1','平日',60,60,$3,$3,now(),$3 * 60)",
                aid,
                date.fromisoformat(d),
                trips,
            )


async def _seed_gated_agency(pool, aid, *, name, strategy, planned, observed, samples, on_time, delay_min, clamp=0):
    """One agency on 2026-04-01 with every figure the comparability gate reads."""
    async with pool.acquire() as c:
        await c.execute("UPDATE agencies SET agency_name = $1 WHERE agency_id = $2", name, aid)
    await _set_ingest_strategy(pool, aid, strategy)
    await _seed_saved_plan(pool, aid, [("2026-04-01", planned)])
    await _seed_observed_trips(pool, aid, [("2026-04-01", observed)])
    await _seed(
        pool,
        aid,
        dist=[("2026-04-01", samples, samples * delay_min * 60, on_time)],
        feed=("2026-04-01", samples, clamp),
    )


async def test_compute_service_delivered_reads_precomputed_daily_aggregate(net_pool, ch_async_client):
    """The read path sums agg_service_delivered_daily over the range and
    divides against the static schedule's planned count -- no ClickHouse
    access. 5 planned trips, 1 non-executed trip-day precomputed -> 80%."""
    pool, a, b, _cc = net_pool
    await _trust_service_delivered(pool, a, b)
    await _seed_static_schedule(pool, a, service_id="WD", trip_ids=["T1", "T2", "T3", "T4", "T5"], svc_date="20260401")
    await _set_ingest_strategy(pool, a, "static_join")
    await _seed_service_delivered_daily(pool, a, [("2026-04-01", 1)])

    # Agency B: static_join too, with no cancellation in range but one a
    # fortnight earlier, which shows its feed reports them -> reads 100%,
    # not "not available".
    await _seed_static_schedule(pool, b, service_id="WD", trip_ids=["U1", "U2", "U3"], svc_date="20260401")
    await _set_ingest_strategy(pool, b, "static_join")
    await _seed_service_delivered_daily(pool, b, [("2026-03-18", 1)])

    async with pool.acquire() as conn:
        rows = await compute_network_summary(conn, ch_async_client, date(2026, 4, 1), date(2026, 4, 1))

    by = {r["agency_id"]: r for r in rows}
    assert by[a]["planned_trips"] == 5
    assert by[a]["executed_trips"] == 4
    assert by[a]["service_delivered_pct"] == 80.0
    assert by[a]["delivered_missing"] is None
    assert by[b]["planned_trips"] == 3
    assert by[b]["executed_trips"] == 3
    assert by[b]["service_delivered_pct"] == 100.0


async def test_compute_service_delivered_not_available_for_a_feed_that_reports_no_cancellations(
    net_pool, ch_async_client
):
    """A confirmed static_join feed that sent no CANCELED trip or SKIPPED stop
    in the 90 days up to the period's end would read 100% whatever ran, so the
    board reads "not available" with that reason, and vehicle-km delivered,
    the same rate applied to the planned distance, goes with it."""
    pool, a, _b, _cc = net_pool
    await _seed_static_version_summary(pool, a, "v1", trip_count=10, vehicle_km=100.0)
    await _seed_static_schedule(pool, a, service_id="WD", trip_ids=["T1", "T2", "T3"], svc_date="20260401")
    await _set_ingest_strategy(pool, a, "static_join")

    async with pool.acquire() as conn:
        rows = await compute_network_summary(conn, ch_async_client, date(2026, 4, 1), date(2026, 4, 1))

    row = next(r for r in rows if r["agency_id"] == a)
    assert row["planned_trips"] == 3
    assert (row["executed_trips"], row["service_delivered_pct"]) == (None, None)
    assert row["delivered_missing"] == "feed_reports_no_cancellations"
    assert row["planned_vehicle_km"] == 100.0
    assert row["vehicle_km_delivered_pct"] is None


async def test_compute_service_delivered_not_available_when_not_static_join(net_pool, ch_async_client):
    """An agency whose ingest_strategy isn't static_join reads "not available"
    (None), never a misleading 100%, regardless of static-schedule data."""
    pool, _a, b, _cc = net_pool
    await _seed_static_schedule(pool, b, service_id="WD", trip_ids=["U1", "U2", "U3"], svc_date="20260401")
    # b's ingest_strategy is left NULL (net_pool's INSERT never sets it).

    async with pool.acquire() as conn:
        rows = await compute_network_summary(conn, ch_async_client, date(2026, 4, 1), date(2026, 4, 1))

    row = next(r for r in rows if r["agency_id"] == b)
    assert row["planned_trips"] == 3
    assert row["executed_trips"] is None
    assert row["service_delivered_pct"] is None
    # Its ingest strategy cannot carry the trip status at all, whatever a probe says.
    assert row["delivered_missing"] == "feed_cannot_report"


async def test_compute_service_delivered_not_available_for_unconfirmed_static_join_agency(net_pool, ch_async_client):
    """`ingest_strategy == 'static_join'` alone is not sufficient trust: an
    agency with no live verdict in `rt_field_coverage_probes` must still read
    "not available" even with real planned trips and precomputed
    non-executed data -- only `scripts/probe_rt_field_coverage.py --record`
    confirming a live feed's real coverage (never an empty/overnight capture)
    earns that trust. Deletes the fixture's recorded verdicts so this holds
    independently of what the fixture set up."""
    pool, a, _b, _cc = net_pool
    async with pool.acquire() as c:
        await c.execute("DELETE FROM rt_field_coverage_probes")
    await _seed_static_schedule(pool, a, service_id="WD", trip_ids=["T1", "T2"], svc_date="20260401")
    await _set_ingest_strategy(pool, a, "static_join")
    await _seed_service_delivered_daily(pool, a, [("2026-04-01", 1)])

    async with pool.acquire() as conn:
        rows = await compute_network_summary(conn, ch_async_client, date(2026, 4, 1), date(2026, 4, 1))

    row = next(r for r in rows if r["agency_id"] == a)
    assert row["planned_trips"] == 2
    assert row["executed_trips"] is None
    assert row["service_delivered_pct"] is None
    assert row["delivered_missing"] == "feed_unverified"


async def test_compute_service_delivered_no_static_schedule_is_not_available(net_pool, ch_async_client):
    """No static schedule loaded (planned_trips == 0) reads "not available",
    never a divide-by-zero 100%, even for a static_join agency with
    precomputed non-executed rows."""
    pool, a, _b, _cc = net_pool
    await _set_ingest_strategy(pool, a, "static_join")
    await _seed_service_delivered_daily(pool, a, [("2026-04-01", 1)])

    async with pool.acquire() as conn:
        rows = await compute_network_summary(conn, ch_async_client, date(2026, 4, 1), date(2026, 4, 1))

    row = next(r for r in rows if r["agency_id"] == a)
    assert row["planned_trips"] == 0
    assert row["executed_trips"] is None
    assert row["service_delivered_pct"] is None
    assert row["delivered_missing"] == "no_plan"


async def test_compute_service_delivered_clamps_non_executed_exceeding_planned(net_pool, ch_async_client):
    """A non_executed_trips total exceeding planned_trips (feed drift, or a
    RT-only ADDED trip marked CANCELED) must clamp executed_trips at 0, never
    go negative."""
    pool, a, _b, _cc = net_pool
    await _trust_service_delivered(pool, a)
    await _seed_static_schedule(pool, a, service_id="WD", trip_ids=["T1", "T2"], svc_date="20260401")
    await _set_ingest_strategy(pool, a, "static_join")
    await _seed_service_delivered_daily(pool, a, [("2026-04-01", 5)])

    async with pool.acquire() as conn:
        rows = await compute_network_summary(conn, ch_async_client, date(2026, 4, 1), date(2026, 4, 1))

    row = next(r for r in rows if r["agency_id"] == a)
    assert row["planned_trips"] == 2
    assert row["executed_trips"] == 0
    assert row["service_delivered_pct"] == 0.0


async def test_compute_supply_metrics_vehicle_km_delivered_uses_service_delivered_ratio(net_pool, ch_async_client):
    """vehicle_km_delivered_pct is the service-delivered (executed/planned
    trip) ratio applied to the current static-version's planned vehicle-km,
    once BOTH are available."""
    pool, a, _b, _cc = net_pool
    await _seed_static_version_summary(pool, a, "v1", trip_count=10, vehicle_km=100.0)
    await _seed_static_schedule(pool, a, service_id="WD", trip_ids=["T1", "T2", "T3", "T4", "T5"], svc_date="20260401")
    await _set_ingest_strategy(pool, a, "static_join")
    await _seed_service_delivered_daily(pool, a, [("2026-04-01", 1)])  # 4/5 executed -> 80%

    async with pool.acquire() as conn:
        rows = await compute_network_summary(conn, ch_async_client, date(2026, 4, 1), date(2026, 4, 1))

    row = next(r for r in rows if r["agency_id"] == a)
    assert row["static_version_id"] == "v1"
    assert row["planned_trip_count"] == 10
    assert row["planned_vehicle_km"] == 100.0
    assert row["vehicle_km_delivered_pct"] == 80.0


async def test_compute_supply_metrics_falls_back_to_trip_count_only_without_shapes(net_pool, ch_async_client):
    """No shapes.txt loaded for this static version (vehicle_km NULL) ->
    vehicle_km_delivered_pct reads None (trip-count-only fallback) even
    though the service-delivered ratio IS available -- planned_trip_count
    alone still carries the headline."""
    pool, a, _b, _cc = net_pool
    await _seed_static_version_summary(pool, a, "v1", trip_count=10, vehicle_km=None)
    await _seed_static_schedule(pool, a, service_id="WD", trip_ids=["T1", "T2", "T3", "T4", "T5"], svc_date="20260401")
    await _set_ingest_strategy(pool, a, "static_join")
    await _seed_service_delivered_daily(pool, a, [("2026-04-01", 1)])

    async with pool.acquire() as conn:
        rows = await compute_network_summary(conn, ch_async_client, date(2026, 4, 1), date(2026, 4, 1))

    row = next(r for r in rows if r["agency_id"] == a)
    assert row["planned_trip_count"] == 10
    assert row["planned_vehicle_km"] is None
    assert row["vehicle_km_delivered_pct"] is None


async def test_compute_supply_metrics_not_available_without_any_recorded_version(net_pool, ch_async_client):
    """No agg_static_version_summary row at all for this agency -> every
    supply field reads None, never a misleading 0/100%."""
    pool, a, _b, _cc = net_pool

    async with pool.acquire() as conn:
        rows = await compute_network_summary(conn, ch_async_client, date(2026, 4, 1), date(2026, 4, 1))

    row = next(r for r in rows if r["agency_id"] == a)
    assert row["static_version_id"] is None
    assert row["planned_trip_count"] is None
    assert row["planned_vehicle_km"] is None
    assert row["vehicle_km_delivered_pct"] is None


async def test_compute_supply_metrics_reads_most_recently_computed_version(net_pool, ch_async_client):
    """Two versions recorded for one agency (a past reload's row persists
    alongside the current one -- see pipeline.reports.supply) -> the network
    summary headline reads the MOST RECENTLY COMPUTED one, not the
    alphabetically- or numerically-first static_version_id."""
    pool, a, _b, _cc = net_pool
    await _seed_static_version_summary(
        pool, a, "v1_old", trip_count=5, vehicle_km=50.0, computed_at=datetime(2026, 3, 1, tzinfo=timezone.utc)
    )
    await _seed_static_version_summary(
        pool, a, "v2_new", trip_count=8, vehicle_km=80.0, computed_at=datetime(2026, 4, 1, tzinfo=timezone.utc)
    )

    async with pool.acquire() as conn:
        rows = await compute_network_summary(conn, ch_async_client, date(2026, 4, 1), date(2026, 4, 1))

    row = next(r for r in rows if r["agency_id"] == a)
    assert row["static_version_id"] == "v2_new"
    assert row["planned_trip_count"] == 8
    assert row["planned_vehicle_km"] == 80.0


async def test_compute_rollups_ranking_and_freshness(net_pool, ch_client, ch_async_client):
    pool, a, b, cc = net_pool
    await _seed(
        pool,
        a,
        dist=[("2026-04-01", 100, 60000, 50), ("2026-04-02", 100, 60000, 50)],
        feed=("2026-04-01", 1000, 5),
        updates_at=datetime(2026, 4, 2, 2, 37, tzinfo=timezone.utc),
    )
    # B's dist lags its newest completed updates day (2026-04-01 < 2026-04-02) → stale.
    await _seed(
        pool,
        b,
        dist=[("2026-04-01", 100, 12000, 100)],
        feed=("2026-04-02", 500, 50),
        updates_at=datetime(2026, 4, 2, 2, 37, tzinfo=timezone.utc),
    )
    # Agency C: no data in range at all.
    from tests.conftest import mirror_updates_to_ch

    mirror_updates_to_ch(ch_client, a)
    mirror_updates_to_ch(ch_client, b)

    async with pool.acquire() as conn:
        rows = await compute_network_summary(conn, ch_async_client, date(2026, 4, 1), date(2026, 4, 7))

    by = {r["agency_id"]: r for r in rows}
    assert by[a]["avg_delay_min"] == 10.0
    assert by[a]["on_time_pct"] == 50.0
    assert by[a]["samples"] == 200
    assert isinstance(by[a]["avg_delay_min"], float)
    assert by[a]["clamp_pct"] == round(5 / 1000 * 100, 2)
    assert by[a]["is_stale"] is False
    assert by[a]["data_from"] == "2026-04-01"
    assert by[a]["data_to"] == "2026-04-02"
    assert by[b]["avg_delay_min"] == 2.0
    assert by[b]["on_time_pct"] == 100.0
    assert by[b]["is_stale"] is True
    assert by[cc]["avg_delay_min"] is None
    assert by[cc]["samples"] == 0
    assert by[cc]["clamp_pct"] is None
    assert by[cc]["data_from"] is None
    assert by[cc]["data_to"] is None
    # No timetable and no ingest strategy: nothing to measure coverage against.
    assert (by[a]["comparable"], by[a]["comparable_reason"]) == (False, "coverage_unknown")
    assert (by[b]["comparable"], by[b]["comparable_reason"]) == (False, "coverage_unknown")
    assert (by[cc]["comparable"], by[cc]["comparable_reason"]) == (False, "no_data")
    # None can be ranked, so they come in agency_id order; the board names them.
    assert [r["agency_id"] for r in rows] == [a, b, cc]


async def test_compute_network_summary_falls_back_to_latest_completed_day_when_today_has_rows(
    net_pool, ch_client, ch_async_client
):
    """An agency ingesting continuously (a completed day AFTER its agg's
    newest day, PLUS a row from right now) must still be flagged stale —
    is_stale must not silently flip to False just because the unconditional
    MAX(captured_at) happens to land on today (the normal, healthy,
    continuously-ingesting case in production).

    Computing MAX(captured_at) over the whole table and accepting it only if
    it is already before today's JST midnight would never fall back to the
    latest prior completed day when today also has rows, defeating staleness
    detection under normal conditions.
    """
    pool, a, _b, _cc = net_pool
    # agg only knows about 2026-04-01 ...
    await _seed(pool, a, dist=[("2026-04-01", 100, 6000, 50)])
    async with pool.acquire() as c:
        # ... but live `updates` has a LATER completed day (04-03) the agg
        # hasn't caught up to yet, plus a row from right now (still-ingesting).
        await c.execute(
            "INSERT INTO updates (agency_id, file_name, captured_at, trip_id, service_type, "
            "scheduled_time, route_code, stop_sequence, dep_delay) "
            "VALUES ($1,'f1.pb',$2,'T1','平日',$3,'R1',1,60)",
            a,
            datetime(2026, 4, 3, 2, 37, tzinfo=timezone.utc),
            time(11, 37),
        )
        await c.execute(
            "INSERT INTO updates (agency_id, file_name, captured_at, trip_id, service_type, "
            "scheduled_time, route_code, stop_sequence, dep_delay) "
            "VALUES ($1,'f_now.pb',now(),'T2','平日',$2,'R1',1,60)",
            a,
            time(11, 37),
        )
    from tests.conftest import mirror_updates_to_ch

    mirror_updates_to_ch(ch_client, a)

    async with pool.acquire() as conn:
        rows = await compute_network_summary(conn, ch_async_client, date(2026, 4, 1), date(2026, 4, 7))

    row = next(r for r in rows if r["agency_id"] == a)
    assert row["is_stale"] is True  # agg (04-01) lags the true latest completed day (04-03)


async def test_compute_network_summary_excludes_deleted_agency(net_pool, ch_async_client):
    pool, a, b, cc = net_pool
    async with pool.acquire() as conn:
        await conn.execute("UPDATE agencies SET deleted_at = now() WHERE agency_id = $1", cc)

    async with pool.acquire() as conn:
        rows = await compute_network_summary(conn, ch_async_client, date(2026, 4, 1), date(2026, 4, 7))

    ids = [r["agency_id"] for r in rows]
    assert cc not in ids
    assert a in ids
    assert b in ids


@pytest.fixture
async def net_client(net_pool, ch_async_client):
    pool, a, b, cc = net_pool
    from api.main import app

    app.state.pool = pool
    app.state.ch_client = ch_async_client
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, pool, a, b, cc


async def test_network_summary_endpoint(net_client, ch_client):
    client, pool, a, b, _cc = net_client
    await _seed(pool, a, dist=[("2026-04-02", 100, 60000, 50)], feed=("2026-04-02", 1000, 5))
    # Agency B: dist lags its completed updates day, NO feed → clamp_pct None, stale.
    await _seed(
        pool, b, dist=[("2026-04-01", 100, 12000, 100)], updates_at=datetime(2026, 4, 2, 2, 37, tzinfo=timezone.utc)
    )
    from tests.conftest import mirror_updates_to_ch

    mirror_updates_to_ch(ch_client, b)
    r = await client.get("/api/network/summary", params={"from": "2026-04-01", "to": "2026-04-07"})
    assert r.status_code == 200
    body = r.json()
    assert body["from"] == "2026-04-01" and body["to"] == "2026-04-07"
    arow = next(x for x in body["agencies"] if x["agency_id"] == a)
    assert arow["avg_delay_min"] == 10.0
    assert set(arow) >= {
        "agency_id",
        "agency_name",
        "avg_delay_min",
        "on_time_pct",
        "samples",
        "raw_samples",
        "clamp_count",
        "clamp_pct",
        "is_stale",
        "planned_trips",
        "executed_trips",
        "service_delivered_pct",
        "covered_days",
        "window_days",
        "delivered_missing",
        "has_ridership_weights",
        "weighted_on_time_pct",
    }
    brow = next(x for x in body["agencies"] if x["agency_id"] == b)
    assert brow["clamp_pct"] is None
    assert brow["is_stale"] is True


async def test_network_summary_says_how_many_of_the_window_days_the_timetable_covers(net_client):
    """The board's rates count only the days a timetable plans, so each row
    carries that count beside the window's length: a reader can then tell an
    agency whose rate spans 1 of 7 days from one whose rate spans all 7."""
    client, pool, a, b, _cc = net_client
    await _seed_static_schedule(pool, a, service_id="WD", trip_ids=["T1", "T2"], svc_date="20260403")
    await _set_ingest_strategy(pool, a, "static_join")

    r = await client.get("/api/network/summary", params={"from": "2026-04-01", "to": "2026-04-07"})
    assert r.status_code == 200
    by = {x["agency_id"]: x for x in r.json()["agencies"]}
    assert (by[a]["planned_trips"], by[a]["covered_days"], by[a]["window_days"]) == (2, 1, 7)
    assert (by[b]["planned_trips"], by[b]["covered_days"], by[b]["window_days"]) == (0, 0, 7)


async def test_network_summary_keeps_the_delay_figures_when_the_planned_figures_fail(net_client, monkeypatch):
    """The planned figures failing in Postgres, as they do on a schema behind
    its migrations, leave every agency without a plan; the delay and feed
    figures from the other tables still answer."""
    from pipeline.reports import service_delivered

    client, pool, a, b, _cc = net_client
    await _seed(pool, a, dist=[("2026-04-02", 100, 60000, 50)], feed=("2026-04-02", 1000, 5))
    await _seed_static_schedule(pool, a, service_id="WD", trip_ids=["T1", "T2"], svc_date="20260403")
    monkeypatch.setattr(service_delivered, "_SERVICE_SQL", "SELECT * FROM planned_trips_not_migrated")

    r = await client.get("/api/network/summary", params={"from": "2026-04-01", "to": "2026-04-07"})
    assert r.status_code == 200
    by = {x["agency_id"]: x for x in r.json()["agencies"]}
    assert (by[a]["avg_delay_min"], by[a]["clamp_pct"]) == (10.0, 0.5)
    for aid in (a, b):
        delivered = {k: by[aid][k] for k in ("planned_trips", "executed_trips", "service_delivered_pct")}
        assert delivered == {"planned_trips": 0, "executed_trips": None, "service_delivered_pct": None}
        # Not a plan of nothing: the board must not print 0 planned trips for a read that failed.
        assert by[aid]["delivered_missing"] == "unreadable"
        assert (by[aid]["covered_days"], by[aid]["window_days"], by[aid]["vehicle_km_delivered_pct"]) == (0, 7, None)
        assert (by[aid]["observed_trips"], by[aid]["coverage_pct"], by[aid]["comparable"]) == (None, None, False)
    assert by[a]["comparable_reason"] == "coverage_unknown"
    assert by[b]["comparable_reason"] == "no_data"


async def test_network_summary_includes_definition_metadata(net_client):
    """The board's on_time_pct always reads the exact legacy_60s column (no
    tolerance query param exists on this endpoint) -- the definition block
    must say so explicitly rather than leaving a user to assume it, and must
    match pipeline.reports.definition's resolved legacy values exactly (not
    a hardcoded/duplicated copy of them)."""
    client, _pool, _a, _b, _cc = net_client
    r = await client.get("/api/network/summary", params={"from": "2026-04-01", "to": "2026-04-07"})
    assert r.status_code == 200
    body = r.json()
    definition = body["definition"]
    assert definition["preset"] == "legacy_60s"
    assert definition["early_tolerance_sec"] is None
    assert definition["late_tolerance_sec"] == 60
    assert definition["exclusion_threshold_sec"] == 7200
    assert definition["measurement_point"] == "all_stops_all_observations"
    assert definition["dedup_rule"] == "latest_observation_per_stop_event"


async def test_network_summary_degrades_when_clickhouse_freshness_probe_fails(net_pool):
    """ClickHouse backs ONLY the ``is_stale`` field here —
    every other field (avg_delay_min, on_time_pct, samples, raw_samples,
    clamp_pct, data_from, data_to) comes from Postgres agg_* tables. A
    ClickHouse hiccup on the per-agency freshness probe must degrade that
    agency's ``is_stale`` (via a None live_max — see is_stale's docstring:
    "no completed day / can't determine -> not stale"), not 500/503 the whole
    /api/network/summary response (mirrors api.routers.map's
    today_route_summary freshness try/except; no real ClickHouse needed — a
    client whose `.query` always raises is enough to simulate the hiccup)."""
    pool, a, _b, _cc = net_pool
    await _seed(pool, a, dist=[("2026-04-02", 100, 60000, 50)], feed=("2026-04-02", 1000, 5))

    class _BrokenCh:
        async def query(self, *args, **kwargs):
            raise RuntimeError("simulated ClickHouse outage")

    from api.main import app

    app.state.pool = pool
    app.state.ch_client = _BrokenCh()
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/network/summary", params={"from": "2026-04-01", "to": "2026-04-07"})
    assert r.status_code == 200
    body = r.json()
    arow = next(x for x in body["agencies"] if x["agency_id"] == a)
    assert arow["avg_delay_min"] == 10.0  # Postgres-backed field unaffected by the CH outage
    assert arow["is_stale"] is False  # degraded live_max=None -> is_stale(agg_day, None) is False


async def test_ridership_weighted_on_time_absent_without_a_weight_table(net_pool, ch_async_client):
    """An agency with zero ridership_weights rows has no weighting configured
    at all -- has_ridership_weights must read False and weighted_on_time_pct
    None, never a value silently computed with an implicit weight of 1
    everywhere."""
    pool, a, _b, _cc = net_pool
    await _seed_route_dist(pool, a, "R1", [("2026-04-01", 100, 60000, 50)])

    async with pool.acquire() as conn:
        rows = await compute_network_summary(conn, ch_async_client, date(2026, 4, 1), date(2026, 4, 1))

    row = next(r for r in rows if r["agency_id"] == a)
    assert row["has_ridership_weights"] is False
    assert row["weighted_on_time_pct"] is None


async def test_ridership_weighted_on_time_shifts_toward_the_heavily_weighted_route(net_pool, ch_async_client):
    """R1 is high-ridership (weight 10x) and on-time 90% of the time; R2 is
    low-ridership (weight 1x, the un-weighted default) and on-time only 10%
    of the time -- same sample count each, so the UNWEIGHTED rate sits at the
    midpoint (50%). The ridership-weighted rate must sit closer to R1's own
    90% than the unweighted rate does, i.e. shift toward the low-ridership
    route's poor performance LESS than the unweighted rate would."""
    pool, a, _b, _cc = net_pool
    await _seed_route_dist(pool, a, "R1", [("2026-04-01", 100, 60000, 90)])
    await _seed_route_dist(pool, a, "R2", [("2026-04-01", 100, 60000, 10)])
    await _seed_ridership_weight(pool, a, "R1", 10)
    await _seed_ridership_weight(pool, a, "R2", 1)

    async with pool.acquire() as conn:
        rows = await compute_network_summary(conn, ch_async_client, date(2026, 4, 1), date(2026, 4, 1))

    row = next(r for r in rows if r["agency_id"] == a)
    assert row["on_time_pct"] == 50.0
    assert row["has_ridership_weights"] is True
    # (90*10 + 10*1) / (100*10 + 100*1) * 100 = 910/1100*100 = 82.7
    assert row["weighted_on_time_pct"] == 82.7
    assert abs(row["weighted_on_time_pct"] - 90) < abs(row["on_time_pct"] - 90)


async def test_ridership_weighted_on_time_uses_agency_default_weight_as_fallback(net_pool, ch_async_client):
    """A route with no route-specific ridership_weights row falls back to its
    agency's default (route_code IS NULL) row, not an implicit 1, when one is
    configured."""
    pool, a, _b, _cc = net_pool
    await _seed_route_dist(pool, a, "R1", [("2026-04-01", 100, 60000, 90)])
    await _seed_route_dist(pool, a, "R2", [("2026-04-01", 100, 60000, 10)])
    await _seed_ridership_weight(pool, a, "R1", 10)
    await _seed_ridership_weight(pool, a, None, 5)  # agency default, covers R2

    async with pool.acquire() as conn:
        rows = await compute_network_summary(conn, ch_async_client, date(2026, 4, 1), date(2026, 4, 1))

    row = next(r for r in rows if r["agency_id"] == a)
    # (90*10 + 10*5) / (100*10 + 100*5) * 100 = 950/1500*100 = 63.3
    assert row["weighted_on_time_pct"] == 63.3


async def test_ridership_weighted_on_time_none_when_configured_but_no_samples_in_range(net_pool, ch_async_client):
    """Configured (has_ridership_weights True) but zero agg_route_daily_dist
    samples in the requested range reads None, distinguishable from "not
    configured" only via has_ridership_weights, not by weighted_on_time_pct
    alone."""
    pool, a, _b, _cc = net_pool
    await _seed_ridership_weight(pool, a, "R1", 10)

    async with pool.acquire() as conn:
        rows = await compute_network_summary(conn, ch_async_client, date(2026, 4, 1), date(2026, 4, 1))

    row = next(r for r in rows if r["agency_id"] == a)
    assert row["has_ridership_weights"] is True
    assert row["weighted_on_time_pct"] is None


async def test_network_summary_endpoint_surfaces_ridership_weighting(net_client):
    """End-to-end: the same weighting behavior is reachable through
    GET /api/network/summary, not just the compute layer."""
    client, pool, a, _b, _cc = net_client
    await _seed_route_dist(pool, a, "R1", [("2026-04-01", 100, 60000, 90)])
    await _seed_route_dist(pool, a, "R2", [("2026-04-01", 100, 60000, 10)])
    await _seed_ridership_weight(pool, a, "R1", 10)
    await _seed_ridership_weight(pool, a, "R2", 1)

    r = await client.get("/api/network/summary", params={"from": "2026-04-01", "to": "2026-04-01"})
    assert r.status_code == 200
    arow = next(x for x in r.json()["agencies"] if x["agency_id"] == a)
    assert arow["has_ridership_weights"] is True
    assert arow["weighted_on_time_pct"] == 82.7


async def test_the_board_shows_an_agency_edit_on_its_next_load(net_client, aconn):
    """A deleted agency leaves the board, and new ridership weights show, at
    once rather than after the board's cache expires."""
    from tests.conftest import TEST_ORIGIN
    from tests.fixtures.users import seed_session

    client, pool, a, b, _cc = net_client
    await _seed(pool, a, dist=[("2026-04-02", 100, 60000, 50)])
    sid = (await seed_session(aconn, role="admin")).sid
    params = {"from": "2026-04-01", "to": "2026-04-07"}
    admin = {"cookies": {"sid": sid}, "headers": {"Origin": TEST_ORIGIN}}

    before = (await client.get("/api/network/summary", params=params)).json()["agencies"]
    assert b in {row["agency_id"] for row in before}
    assert next(row for row in before if row["agency_id"] == a)["has_ridership_weights"] is False

    assert (await client.delete(f"/api/agencies/{b}", **admin)).status_code == 204
    weights = {"upsert": [{"route_code": None, "weight": 2.0}]}
    assert (await client.patch(f"/api/admin/agencies/{a}/weights", json=weights, **admin)).status_code == 200

    after = (await client.get("/api/network/summary", params=params)).json()["agencies"]
    assert b not in {row["agency_id"] for row in after}
    assert next(row for row in after if row["agency_id"] == a)["has_ridership_weights"] is True


async def test_the_board_ranks_only_the_agencies_the_data_can_compare(net_pool, ch_async_client):
    """A dense static_join agency is ranked; a sparse one (few trips, so a
    wide interval) and a thinly observed one (low coverage) are not. The
    ranked agency comes first although it runs earliest, and the unranked
    ones follow in agency_id order, never by delay or by the server's idea of
    name order: the board sorts them by name in its own collation."""
    pool, a, b, cc = net_pool
    # 900 of 1,000 planned trips observed, 90% on time over 20,000 stop events.
    await _seed_gated_agency(
        pool,
        a,
        name="Dense",
        strategy="static_join",
        planned=1000,
        observed=900,
        samples=20000,
        on_time=18000,
        delay_min=1,
    )
    # A day no plan covers: its trips are not observed trips of the plan.
    await _seed_observed_trips(pool, a, [("2026-04-02", 500)])
    # 45 of 50 trips observed, but 45 trips cannot pin a 50% rate to +-3 points.
    await _seed_gated_agency(
        pool,
        b,
        name="Zeta sparse",
        strategy="static_join",
        planned=50,
        observed=45,
        samples=900,
        on_time=450,
        delay_min=5,
    )
    # Dense and on time, but the feed reported only 400 of 1,000 planned trips.
    await _seed_gated_agency(
        pool,
        cc,
        name="Mid coverage",
        strategy="static_join",
        planned=1000,
        observed=400,
        samples=20000,
        on_time=18000,
        delay_min=10,
    )

    async with pool.acquire() as conn:
        rows = await compute_network_summary(conn, ch_async_client, date(2026, 4, 1), date(2026, 4, 2))

    by = {r["agency_id"]: r for r in rows}
    assert (by[a]["comparable"], by[a]["comparable_reason"]) == (True, None)
    assert (by[a]["observed_trips"], by[a]["coverage_planned_trips"], by[a]["coverage_pct"]) == (900, 1000, 90.0)
    assert (by[a]["ci_low"], by[a]["ci_high"]) == (87.8, 91.8)
    assert (by[b]["comparable"], by[b]["comparable_reason"]) == (False, "ci_wide")
    assert by[b]["coverage_pct"] == 90.0
    assert by[b]["ci_high"] - by[b]["ci_low"] > 2 * 3.0
    assert (by[cc]["comparable"], by[cc]["comparable_reason"]) == (False, "coverage_low")
    assert by[cc]["coverage_pct"] == 40.0
    assert [r["agency_id"] for r in rows] == [a, b, cc]


async def test_coverage_over_100_stays_visible_and_still_ranks(net_pool, ch_async_client):
    """More trips observed than planned means the feed reported trips the
    plan doesn't name. The figure is not capped, and it is not too little
    data."""
    pool, a, _b, _cc = net_pool
    await _seed_gated_agency(
        pool,
        a,
        name="Extra",
        strategy="static_join",
        planned=400,
        observed=520,
        samples=10000,
        on_time=9000,
        delay_min=2,
    )

    async with pool.acquire() as conn:
        rows = await compute_network_summary(conn, ch_async_client, date(2026, 4, 1), date(2026, 4, 1))

    row = next(r for r in rows if r["agency_id"] == a)
    assert (row["observed_trips"], row["coverage_pct"]) == (520, 130.0)
    assert (row["comparable"], row["comparable_reason"]) == (True, None)


async def test_a_feed_whose_trips_cannot_be_matched_to_the_timetable_is_unranked(net_pool, ch_async_client):
    """aomori_regex parses its trip_ids from the feed's text, so they don't
    match the timetable's and its coverage cannot be measured, however dense
    its data. Nothing bounds its interval either, so none is printed."""
    pool, a, b, cc = net_pool
    await _seed_gated_agency(
        pool,
        a,
        name="Regex",
        strategy="aomori_regex",
        planned=1000,
        observed=900,
        samples=20000,
        on_time=18000,
        delay_min=9,
    )
    await _seed_gated_agency(
        pool,
        b,
        name="Joined",
        strategy="static_join",
        planned=1000,
        observed=900,
        samples=20000,
        on_time=18000,
        delay_min=1,
    )

    async with pool.acquire() as conn:
        rows = await compute_network_summary(conn, ch_async_client, date(2026, 4, 1), date(2026, 4, 1))

    by = {r["agency_id"]: r for r in rows}
    assert (by[a]["comparable"], by[a]["comparable_reason"]) == (False, "coverage_unknown")
    assert (by[a]["observed_trips"], by[a]["coverage_planned_trips"], by[a]["coverage_pct"]) == (None, None, None)
    assert (by[a]["ci_low"], by[a]["ci_high"]) == (None, None)
    assert by[a]["on_time_pct"] == 90.0
    assert by[b]["comparable"] is True
    assert [r["agency_id"] for r in rows] == [b, a, cc]


async def _seed_two_day_agency(pool, aid, *, name, observed_days):
    """A dense static_join agency planning 1,000 trips on each of 2026-04-01
    and 04-02, observed (900 trips, 90% on time) on ``observed_days`` only."""
    async with pool.acquire() as c:
        await c.execute("UPDATE agencies SET agency_name = $1 WHERE agency_id = $2", name, aid)
    await _set_ingest_strategy(pool, aid, "static_join")
    await _seed_saved_plan(pool, aid, [("2026-04-01", 1000), ("2026-04-02", 1000)])
    await _seed_observed_trips(pool, aid, [(d, 900) for d in observed_days])
    await _seed(pool, aid, dist=[(d, 20000, 20000 * 60, 18000) for d in observed_days], feed=("2026-04-01", 1000, 0))


async def test_a_day_no_agency_has_observations_for_yet_counts_against_none(net_client):
    """The period's last day has a plan, but no agency's aggregates have
    reached it: a lag they share, not trips each feed missed. Coverage stops
    at the newest observed day, which the summary names once; the planned
    figure still spans the period."""
    client, pool, a, b, _cc = net_client
    await _seed_two_day_agency(pool, a, name="A", observed_days=["2026-04-01"])
    await _seed_two_day_agency(pool, b, name="B", observed_days=["2026-04-01"])

    r = await client.get("/api/network/summary", params={"from": "2026-04-01", "to": "2026-04-02"})
    assert r.status_code == 200
    body = r.json()
    assert body["coverage_through"] == "2026-04-01"
    by = {x["agency_id"]: x for x in body["agencies"]}
    for aid in (a, b):
        assert by[aid]["planned_trips"] == 2000
        assert (by[aid]["observed_trips"], by[aid]["coverage_planned_trips"]) == (900, 1000)
        assert (by[aid]["coverage_pct"], by[aid]["comparable"]) == (90.0, True)
        assert "coverage_through" not in by[aid]


async def test_a_day_one_feed_missed_counts_against_that_feed(net_pool, ch_async_client):
    pool, a, b, _cc = net_pool
    await _seed_two_day_agency(pool, a, name="A", observed_days=["2026-04-01", "2026-04-02"])
    await _seed_two_day_agency(pool, b, name="B", observed_days=["2026-04-01"])

    async with pool.acquire() as conn:
        rows = await compute_network_summary(conn, ch_async_client, date(2026, 4, 1), date(2026, 4, 2))

    by = {r["agency_id"]: r for r in rows}
    assert (by[a]["coverage_pct"], by[a]["comparable"]) == (90.0, True)
    assert (by[b]["observed_trips"], by[b]["coverage_planned_trips"]) == (900, 2000)
    assert (by[b]["coverage_pct"], by[b]["comparable_reason"]) == (45.0, "coverage_low")


async def test_a_high_implausible_share_is_unranked(net_pool, ch_async_client):
    pool, a, _b, _cc = net_pool
    await _seed_gated_agency(
        pool,
        a,
        name="Noisy",
        strategy="static_join",
        planned=1000,
        observed=900,
        samples=20000,
        on_time=18000,
        delay_min=1,
        clamp=2000,
    )

    async with pool.acquire() as conn:
        rows = await compute_network_summary(conn, ch_async_client, date(2026, 4, 1), date(2026, 4, 1))

    row = next(r for r in rows if r["agency_id"] == a)
    assert row["clamp_pct"] == 10.0
    assert (row["comparable"], row["comparable_reason"]) == (False, "clamp_high")


async def test_network_summary_endpoint_carries_the_gate_and_its_thresholds(net_client):
    client, pool, a, b, cc = net_client
    await _seed_gated_agency(
        pool,
        a,
        name="Dense",
        strategy="static_join",
        planned=1000,
        observed=900,
        samples=20000,
        on_time=18000,
        delay_min=1,
    )
    await _seed_gated_agency(
        pool,
        b,
        name="Sparse",
        strategy="static_join",
        planned=50,
        observed=45,
        samples=900,
        on_time=450,
        delay_min=10,
    )

    r = await client.get("/api/network/summary", params={"from": "2026-04-01", "to": "2026-04-01"})
    assert r.status_code == 200
    body = r.json()
    assert body["comparability"] == {"min_coverage_pct": 70.0, "max_ci_half_width_pp": 3.0, "max_clamp_pct": 5.0}
    assert body["coverage_through"] == "2026-04-01"
    rows = body["agencies"]
    assert [x["agency_id"] for x in rows] == [a, b, cc]
    dense = rows[0]
    assert {k: dense[k] for k in ("coverage_pct", "observed_trips", "ci_low", "ci_high", "comparable")} == {
        "coverage_pct": 90.0,
        "observed_trips": 900,
        "ci_low": 87.8,
        "ci_high": 91.8,
        "comparable": True,
    }
    assert dense["comparable_reason"] is None
    assert (rows[1]["comparable"], rows[1]["comparable_reason"]) == (False, "ci_wide")
