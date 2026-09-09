"""API tests for GET /api/{agency_id}/performance_standards (item 104).

Seeds `route_performance_standards` (migration 0041) directly via asyncpg,
plus whichever underlying aggregate a row's `metric_type` reads from
(`agg_route_headway`/`agg_route_headway_daily` for `ewt_sec`, the same
`static_calendar_dates`/`static_trips`/`agg_service_delivered_daily`/
`agg_static_version_summary` fixture shape `tests/api/test_network.py` uses
for `vehicle_km_delivered_pct`) -- like `test_headway_quality.py`, this
endpoint only ever reads precomputed Postgres aggregates plus the new
config table, so exercising `pipeline.analyze()`'s own population of them
is other tests' job, not this file's.
"""

import os
from datetime import date

import asyncpg
import httpx
import pytest
from httpx import ASGITransport

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://localhost/transit")


@pytest.fixture
async def perf_client(apply_schema):
    from api.main import app

    pool = await asyncpg.create_pool(DATABASE_URL)
    app.state.pool = pool
    row = await pool.fetchrow(
        "INSERT INTO agencies (agency_name, feed_url) VALUES ($1, $2) RETURNING agency_id",
        "Performance Standard Test Agency",
        "http://performance-standard-test.example.com",
    )
    aid = row["agency_id"]

    # ewt_sec fixtures: two high-frequency routes, both scheduled_wait_mean_sec
    # = 200.0, differing only in their pooled actual headway so the resulting
    # ewt_sec lands on a clean, hand-checkable number.
    #
    # A perfectly regular n-gap headway of h seconds (sum_sec=n*h,
    # sumsq_sec2=n*h^2) makes mean_wait_from_moments reduce to the familiar
    # headway/2 (zero variance): mean_headway=h, mean_headway_sq=h^2,
    # mean_wait=h^2/(2h)=h/2.
    # R_AT: two identical 500s gaps -> mean_wait = 500/2 = 250.0 ->
    # ewt_sec = 250.0 - 200.0 = 50.0.
    # R_BELOW: two identical 700s gaps -> mean_wait = 700/2 = 350.0 ->
    # ewt_sec = 350.0 - 200.0 = 150.0.
    await pool.executemany(
        "INSERT INTO agg_route_headway "
        "(agency_id, route_code, scheduled_headway_median_sec, scheduled_samples, "
        " is_high_frequency, scheduled_wait_mean_sec) VALUES ($1,$2,$3,$4,$5,$6)",
        [
            (aid, "R_AT", 400.0, 10, True, 200.0),
            (aid, "R_BELOW", 400.0, 10, True, 200.0),
        ],
    )
    await pool.executemany(
        "INSERT INTO agg_route_headway_daily "
        "(agency_id, route_code, date, actual_headway_median_sec, actual_samples, "
        " actual_headway_sum_sec, actual_headway_sumsq_sec2, long_gap_count) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8)",
        [
            (aid, "R_AT", date(2026, 4, 1), 500.0, 2, 1000.0, 500000.0, 0),
            (aid, "R_BELOW", date(2026, 4, 1), 700.0, 2, 1400.0, 980000.0, 0),
        ],
    )

    # vehicle_km_delivered_pct fixtures: agency-level 4/5 executed trips
    # (80% service-delivered ratio) applied to a 100.0 planned-vehicle-km
    # static version -> vehicle_km_delivered_pct == 80.0 (same fixture shape
    # as tests/api/test_network.py's
    # test_compute_supply_metrics_vehicle_km_delivered_uses_service_delivered_ratio).
    await pool.execute("UPDATE agencies SET ingest_strategy = 'static_join' WHERE agency_id = $1", aid)
    await pool.execute(
        "INSERT INTO static_calendar_dates (agency_id, service_id, date, exception_type) VALUES ($1,$2,$3,1)",
        aid,
        "WD",
        "20260401",
    )
    await pool.executemany(
        "INSERT INTO static_trips (agency_id, trip_id, route_id, service_id) VALUES ($1,$2,'R1',$3)",
        [(aid, tid, "WD") for tid in ["T1", "T2", "T3", "T4", "T5"]],
    )
    await pool.execute(
        "INSERT INTO agg_service_delivered_daily (agency_id, date, non_executed_trips) VALUES ($1,$2,$3)",
        aid,
        date(2026, 4, 1),
        1,
    )
    await pool.execute(
        "INSERT INTO agg_static_version_summary (agency_id, static_version_id, trip_count, vehicle_km) "
        "VALUES ($1,$2,$3,$4)",
        aid,
        "v1",
        10,
        100.0,
    )

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, aid, pool
    async with pool.acquire() as conn:
        await conn.execute(
            "TRUNCATE agencies, agg_route_headway, agg_route_headway_daily, "
            "route_performance_standards, static_calendar_dates, static_trips, "
            "agg_service_delivered_daily, agg_static_version_summary CASCADE"
        )
    await pool.close()


async def _seed_standard(pool, aid, route_code, metric_type, threshold_value, bonus_malus_rate):
    await pool.execute(
        "INSERT INTO route_performance_standards "
        "(agency_id, route_code, metric_type, threshold_value, bonus_malus_rate) "
        "VALUES ($1,$2,$3,$4,$5)",
        aid,
        route_code,
        metric_type,
        threshold_value,
        bonus_malus_rate,
    )


async def test_route_exactly_at_ewt_threshold_yields_zero_bonus_deduction(perf_client):
    """R_AT's pooled actual ewt_sec is exactly 50.0 -- a standard configured
    with threshold_value=50.0 must read achievement_rate==1.0 and
    estimated_bonus_deduction==0.0, not a near-zero float only."""
    client, aid, pool = perf_client
    await _seed_standard(pool, aid, "R_AT", "ewt_sec", 50.0, 1000.0)

    r = await client.get(f"/api/{aid}/performance_standards?from=2026-04-01&to=2026-04-01")
    assert r.status_code == 200
    row = next(x for x in r.json()["rows"] if x["route_code"] == "R_AT")

    assert row["metric_type"] == "ewt_sec"
    assert row["metric_scope"] == "route"
    assert row["actual_value"] == pytest.approx(50.0, abs=1e-6)
    assert row["achievement_rate"] == pytest.approx(1.0, abs=1e-9)
    assert row["estimated_bonus_deduction"] == pytest.approx(0.0, abs=1e-9)


async def test_route_below_ewt_standard_yields_expected_deduction(perf_client):
    """R_BELOW's pooled actual ewt_sec is 150.0 against a threshold_value of
    100.0: relative_deviation = (100 - 150) / 100 = -0.5 (worse than
    standard, since ewt_sec is lower-is-better) ->
    estimated_bonus_deduction = 1000.0 * -0.5 = -500.0 (a deduction, i.e.
    strictly negative)."""
    client, aid, pool = perf_client
    await _seed_standard(pool, aid, "R_BELOW", "ewt_sec", 100.0, 1000.0)

    r = await client.get(f"/api/{aid}/performance_standards?from=2026-04-01&to=2026-04-01")
    assert r.status_code == 200
    row = next(x for x in r.json()["rows"] if x["route_code"] == "R_BELOW")

    assert row["actual_value"] == pytest.approx(150.0, abs=1e-6)
    assert row["achievement_rate"] == pytest.approx(0.5, abs=1e-9)
    assert row["estimated_bonus_deduction"] == pytest.approx(-500.0, abs=1e-6)
    assert row["estimated_bonus_deduction"] < 0


async def test_route_above_ewt_standard_yields_positive_bonus(perf_client):
    """R_AT's actual ewt_sec (50.0) beats a looser threshold_value of 100.0:
    relative_deviation = (100 - 50) / 100 = 0.5 (better than standard) ->
    a strictly positive estimated bonus."""
    client, aid, pool = perf_client
    await _seed_standard(pool, aid, "R_AT", "ewt_sec", 100.0, 1000.0)

    r = await client.get(f"/api/{aid}/performance_standards?from=2026-04-01&to=2026-04-01")
    assert r.status_code == 200
    row = next(x for x in r.json()["rows"] if x["route_code"] == "R_AT")

    assert row["achievement_rate"] == pytest.approx(1.5, abs=1e-9)
    assert row["estimated_bonus_deduction"] == pytest.approx(500.0, abs=1e-6)
    assert row["estimated_bonus_deduction"] > 0


async def test_vehicle_km_delivered_pct_at_threshold_is_zero_and_scoped_to_agency(perf_client):
    """The agency-wide vehicle_km_delivered_pct fixture resolves to exactly
    80.0 (4/5 executed trips, see module docstring). A route standard
    configured at threshold_value=80.0 must read achievement_rate==1.0 /
    estimated_bonus_deduction==0.0, and must be labeled metric_scope=="agency"
    since this figure has no per-route breakdown."""
    client, aid, pool = perf_client
    await _seed_standard(pool, aid, "R_VKM", "vehicle_km_delivered_pct", 80.0, 2000.0)

    r = await client.get(f"/api/{aid}/performance_standards?from=2026-04-01&to=2026-04-01")
    assert r.status_code == 200
    row = next(x for x in r.json()["rows"] if x["route_code"] == "R_VKM")

    assert row["metric_scope"] == "agency"
    assert row["actual_value"] == pytest.approx(80.0, abs=1e-6)
    assert row["achievement_rate"] == pytest.approx(1.0, abs=1e-9)
    assert row["estimated_bonus_deduction"] == pytest.approx(0.0, abs=1e-9)


async def test_vehicle_km_delivered_pct_below_threshold_yields_deduction(perf_client):
    """threshold_value=90.0 against the same 80.0 agency-wide actual:
    relative_deviation = (80 - 90) / 90 (higher-is-better) -> negative ->
    a strictly negative estimated deduction."""
    client, aid, pool = perf_client
    await _seed_standard(pool, aid, "R_VKM_BELOW", "vehicle_km_delivered_pct", 90.0, 2000.0)

    r = await client.get(f"/api/{aid}/performance_standards?from=2026-04-01&to=2026-04-01")
    assert r.status_code == 200
    row = next(x for x in r.json()["rows"] if x["route_code"] == "R_VKM_BELOW")

    expected_deviation = (80.0 - 90.0) / 90.0
    assert row["achievement_rate"] == pytest.approx(1.0 + expected_deviation, abs=1e-9)
    assert row["estimated_bonus_deduction"] == pytest.approx(2000.0 * expected_deviation, abs=1e-6)
    assert row["estimated_bonus_deduction"] < 0


async def test_zero_threshold_yields_undefined_achievement_rather_than_crash(perf_client):
    """threshold_value=0.0 makes the relative-deviation ratio's denominator
    meaningless -- achievement_rate/estimated_bonus_deduction must both
    surface None (undefined), never a ZeroDivisionError or a fabricated 0."""
    client, aid, pool = perf_client
    await _seed_standard(pool, aid, "R_AT", "ewt_sec", 0.0, 1000.0)

    r = await client.get(f"/api/{aid}/performance_standards?from=2026-04-01&to=2026-04-01")
    assert r.status_code == 200
    row = next(x for x in r.json()["rows"] if x["route_code"] == "R_AT")

    assert row["actual_value"] == pytest.approx(50.0, abs=1e-6)
    assert row["achievement_rate"] is None
    assert row["estimated_bonus_deduction"] is None


async def test_not_high_frequency_route_standard_yields_none_actual_value(perf_client):
    """A configured standard on a route with no resolvable ewt_sec (no
    agg_route_headway row at all here) must still be returned -- "insufficient
    data", not silently omitted or a crash."""
    client, aid, pool = perf_client
    await _seed_standard(pool, aid, "R_UNKNOWN", "ewt_sec", 50.0, 1000.0)

    r = await client.get(f"/api/{aid}/performance_standards?from=2026-04-01&to=2026-04-01")
    assert r.status_code == 200
    row = next(x for x in r.json()["rows"] if x["route_code"] == "R_UNKNOWN")

    assert row["actual_value"] is None
    assert row["achievement_rate"] is None
    assert row["estimated_bonus_deduction"] is None


async def test_route_filter_excluding_configured_route_still_resolves_actual_value(perf_client):
    """The page-level `routes` filter (the Analysis tab's route dropdown,
    forwarded unchanged into ctx.routes) must not blank a configured
    standard's actual_value to None just because that route happens to be
    excluded from the filter -- that's a different state than genuinely
    insufficient data (see test_not_high_frequency_route_standard_yields_
    none_actual_value above) and must stay distinguishable from it.

    R_BELOW has real ewt_sec data (150.0, see module docstring). Filtering
    `routes` down to only R_AT (excluding R_BELOW) must still resolve
    R_BELOW's actual_value/achievement_rate/estimated_bonus_deduction from
    its own real data, not None."""
    client, aid, pool = perf_client
    await _seed_standard(pool, aid, "R_BELOW", "ewt_sec", 100.0, 1000.0)

    r = await client.get(f"/api/{aid}/performance_standards?from=2026-04-01&to=2026-04-01&routes=R_AT")
    assert r.status_code == 200
    row = next(x for x in r.json()["rows"] if x["route_code"] == "R_BELOW")

    assert row["actual_value"] == pytest.approx(150.0, abs=1e-6)
    assert row["achievement_rate"] == pytest.approx(0.5, abs=1e-9)
    assert row["estimated_bonus_deduction"] == pytest.approx(-500.0, abs=1e-6)


async def test_empty_agency_returns_empty_rows(perf_client):
    client, _aid, pool = perf_client
    row = await pool.fetchrow(
        "INSERT INTO agencies (agency_name, feed_url) VALUES ($1, $2) RETURNING agency_id",
        "Performance Standard Empty Agency",
        "http://performance-standard-empty.example.com",
    )
    empty_aid = row["agency_id"]

    r = await client.get(f"/api/{empty_aid}/performance_standards")
    assert r.status_code == 200
    assert r.json()["rows"] == []


async def test_response_carries_simulation_disclaimer_in_both_locales(perf_client):
    """The 'simulation, not an invoice' caveat must always be present,
    localized per the request's Accept-Language, in both supported
    languages."""
    client, aid, pool = perf_client
    await _seed_standard(pool, aid, "R_AT", "ewt_sec", 50.0, 1000.0)

    r_ja = await client.get(
        f"/api/{aid}/performance_standards?from=2026-04-01&to=2026-04-01",
        headers={"Accept-Language": "ja"},
    )
    r_en = await client.get(
        f"/api/{aid}/performance_standards?from=2026-04-01&to=2026-04-01",
        headers={"Accept-Language": "en"},
    )
    assert r_ja.status_code == 200 and r_en.status_code == 200
    assert "シミュレーション" in r_ja.json()["disclaimer"]
    assert "simulation" in r_en.json()["disclaimer"].lower()
    assert r_ja.json()["disclaimer"] != r_en.json()["disclaimer"]
