"""API tests for GET /api/{agency_id}/performance_standards.

Seeds `route_performance_standards` (migrations 0041, 0089 and 0094)
directly via asyncpg, plus whichever figures a row's `metric_type` reads:
`agg_route_headway`/`agg_route_headway_daily` for `ewt_sec`, and the
`static_calendar_dates`/`static_trips`/`agg_service_delivered_daily`/
`agg_route_daily`/`agg_static_version_summary` shape of one planned day for
`service_delivered_pct` and `vehicle_km_delivered_pct`. Those read
precomputed Postgres aggregates only.

The `on_time_pct` tests read the Routes table's own figures, so they seed
the way its tests do: `agg_route_daily_dist` rows directly, or observations
that run through `analyze()` (the agg path) or are mirrored to ClickHouse
and scanned live under a time band (the live path).
"""

from datetime import date

import httpx
import pytest
from httpx import ASGITransport

from tests.api.test_reports import (  # noqa: F401 -- reports_app/reports_client are fixtures
    _load_for_path,
    _seed_route_at,
    reports_app,
    reports_client,
)
from tests.conftest import _test_pool, confirm_rt_field_coverage

_DELIVERED_DAY = date(2026, 4, 1)


@pytest.fixture
async def perf_client(apply_schema):
    from api.main import app

    pool = await _test_pool()
    app.state.pool = pool
    row = await pool.fetchrow(
        "INSERT INTO agencies (agency_name, feed_url) VALUES ($1, $2) RETURNING agency_id",
        "Performance Standard Test Agency",
        "http://performance-standard-test.example.com",
    )
    aid = row["agency_id"]
    # This fixture represents a feed already confirmed by the RT probe;
    # production trusts only agencies with a recorded live-coverage verdict.
    await confirm_rt_field_coverage(pool, aid)

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
    # The day's own timetable runs both every 400 s, ten gaps: a 200 s scheduled wait.
    every_400_sec = (400.0, True, 4000.0, 1600000.0)
    await pool.executemany(
        "INSERT INTO agg_route_headway_daily "
        "(agency_id, route_code, date, actual_headway_median_sec, actual_samples, "
        " actual_headway_sum_sec, actual_headway_sumsq_sec2, long_gap_count, scheduled_headway_median_sec, "
        " is_high_frequency, scheduled_headway_sum_sec, scheduled_headway_sumsq_sec2) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)",
        [
            (aid, "R_AT", date(2026, 4, 1), 500.0, 2, 1000.0, 500000.0, 0, *every_400_sec),
            (aid, "R_BELOW", date(2026, 4, 1), 700.0, 2, 1400.0, 980000.0, 0, *every_400_sec),
        ],
    )

    # service_delivered_pct and vehicle_km_delivered_pct fixtures, one planned
    # day: RD plans 100 trips, 20 of them reported not run, and is observed
    # 150 times -> 80.0%. RQ plans 100 too, but is observed 10 times. RTHIN
    # plans 5. The agency's plan has a 100.0 planned-vehicle-km static version.
    await pool.execute("UPDATE agencies SET ingest_strategy = 'static_join' WHERE agency_id = $1", aid)
    await pool.execute(
        "INSERT INTO static_calendar_dates (agency_id, service_id, date, exception_type) VALUES ($1,$2,$3,1)",
        aid,
        "WD",
        "20260401",
    )
    await pool.executemany(
        "INSERT INTO static_trips (agency_id, trip_id, route_id, service_id) VALUES ($1,$2,$3,'WD')",
        [
            (aid, f"{route}-{i}", route)
            for route, count in (("RD", 100), ("RQ", 100), ("RTHIN", 5))
            for i in range(count)
        ],
    )
    await pool.execute(
        "INSERT INTO agg_service_delivered_daily (agency_id, date, route_code, non_executed_trips) "
        "VALUES ($1,$2,$3,$4)",
        aid,
        _DELIVERED_DAY,
        "RD",
        20,
    )
    await pool.executemany(
        "INSERT INTO agg_route_daily (agency_id, date, route_code, service_type, avg_delay_sec, worst_delay_sec, "
        "trips_observed, samples, last_seen_at) VALUES ($1, $2, $3, '', 30, 30, 1, $4, NOW())",
        [(aid, _DELIVERED_DAY, "RD", 150), (aid, _DELIVERED_DAY, "RQ", 10), (aid, _DELIVERED_DAY, "RTHIN", 150)],
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
            "route_performance_standards, static_calendar_dates, static_trips, agg_route_daily, "
            "agg_service_delivered_daily, agg_static_version_summary, planned_trips_daily CASCADE"
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
    assert row["actual_value"] == pytest.approx(50.0, abs=1e-6)
    assert row["achievement_rate"] == pytest.approx(1.0, abs=1e-9)
    assert row["estimated_bonus_deduction"] == pytest.approx(0.0, abs=1e-9)


async def test_an_ewt_standard_reads_every_day_of_the_range_whatever_the_page_dow(perf_client):
    """The endpoint reads dates only: a page-wide weekday filter must not empty
    a configured standard's actual value."""
    client, aid, pool = perf_client
    await _seed_standard(pool, aid, "R_AT", "ewt_sec", 50.0, 1000.0)

    r = await client.get(f"/api/{aid}/performance_standards?from=2026-04-01&to=2026-04-01&dow=weekend")
    assert r.status_code == 200
    row = next(x for x in r.json()["rows"] if x["route_code"] == "R_AT")
    assert row["actual_value"] == pytest.approx(50.0, abs=1e-6)


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


async def test_vehicle_km_delivered_pct_at_threshold_is_zero_and_read_on_the_routes_own_trips(perf_client):
    """RD's own trips resolve to exactly 80.0 (80 of its 100 planned trips
    run), whatever the agency's other routes did. A standard at 80.0 reads
    achievement_rate==1.0 and estimated_bonus_deduction==0.0."""
    client, aid, pool = perf_client
    await _seed_standard(pool, aid, "RD", "vehicle_km_delivered_pct", 80.0, 2000.0)

    r = await client.get(f"/api/{aid}/performance_standards?from=2026-04-01&to=2026-04-01")
    assert r.status_code == 200
    row = next(x for x in r.json()["rows"] if x["route_code"] == "RD")

    assert row["actual_value"] == pytest.approx(80.0, abs=1e-6)
    assert row["achievement_rate"] == pytest.approx(1.0, abs=1e-9)
    assert row["estimated_bonus_deduction"] == pytest.approx(0.0, abs=1e-9)
    assert row["not_judged"] is None


async def test_vehicle_km_delivered_pct_below_threshold_yields_deduction(perf_client):
    """threshold_value=90.0 against RD's 80.0:
    relative_deviation = (80 - 90) / 90 (higher-is-better) -> negative ->
    a strictly negative estimated deduction."""
    client, aid, pool = perf_client
    await _seed_standard(pool, aid, "RD", "vehicle_km_delivered_pct", 90.0, 2000.0)

    r = await client.get(f"/api/{aid}/performance_standards?from=2026-04-01&to=2026-04-01")
    assert r.status_code == 200
    row = next(x for x in r.json()["rows"] if x["route_code"] == "RD")

    expected_deviation = (80.0 - 90.0) / 90.0
    assert row["achievement_rate"] == pytest.approx(1.0 + expected_deviation, abs=1e-9)
    assert row["estimated_bonus_deduction"] == pytest.approx(2000.0 * expected_deviation, abs=1e-6)
    assert row["estimated_bonus_deduction"] < 0


async def test_vehicle_km_delivered_pct_needs_the_timetables_route_shapes(perf_client):
    client, aid, pool = perf_client
    await pool.execute("UPDATE agg_static_version_summary SET vehicle_km = NULL WHERE agency_id = $1", aid)
    await _seed_standard(pool, aid, "RD", "vehicle_km_delivered_pct", 80.0, 0.0)
    await _seed_standard(pool, aid, "RD", "service_delivered_pct", 80.0, 0.0)

    r = await client.get(f"/api/{aid}/performance_standards?from=2026-04-01&to=2026-04-01")
    by_metric = {x["metric_type"]: x for x in r.json()["rows"]}

    assert by_metric["vehicle_km_delivered_pct"]["actual_value"] is None
    assert by_metric["vehicle_km_delivered_pct"]["not_judged"] == _not_judged("no_shapes")
    assert by_metric["service_delivered_pct"]["actual_value"] == 80.0


# ── service-delivered targets ─────────────────────────────────────────────


def _not_judged(reason, *, observations=None, floor=None, planned_trips=None):
    return {"reason": reason, "observations": observations, "floor": floor, "planned_trips": planned_trips}


async def test_a_service_delivered_target_is_judged_on_the_routes_own_share(perf_client):
    client, aid, pool = perf_client
    await _seed_standard(pool, aid, "RD", "service_delivered_pct", 80.0, 1000.0)
    await _seed_standard(pool, aid, "RD", "on_time_pct", 50.0, 0.0)

    r = await client.get(f"/api/{aid}/performance_standards?from=2026-04-01&to=2026-04-01")
    assert r.status_code == 200
    body = r.json()
    row = next(x for x in body["rows"] if x["metric_type"] == "service_delivered_pct")

    assert row["route_code"] == "RD"
    assert row["actual_value"] == 80.0
    assert row["meets"] is True
    assert row["not_judged"] is None
    assert row["achievement_rate"] == pytest.approx(1.0)
    assert row["estimated_bonus_deduction"] == pytest.approx(0.0)


async def test_a_service_delivered_target_reads_the_dates_only(perf_client):
    """The share counts the route's planned trips over the period's days, so
    neither the page's weekday filter nor its route filter moves it."""
    client, aid, pool = perf_client
    await _seed_standard(pool, aid, "RD", "service_delivered_pct", 80.0, 0.0)

    body = (
        await client.get(f"/api/{aid}/performance_standards?from=2026-04-01&to=2026-04-01&dow=weekend&routes=RQ")
    ).json()

    assert body["rows"][0]["actual_value"] == 80.0
    assert [k for k, v in body["scope_applied"].items() if v] == ["from", "to"]


async def test_a_service_delivered_target_is_not_judged_on_thin_plans_or_rarely_reported_routes(perf_client):
    from pipeline.reports.rankings import RANKING_MIN_SAMPLES

    client, aid, pool = perf_client
    for code in ("RTHIN", "RQ", "UNPLANNED"):
        await _seed_standard(pool, aid, code, "service_delivered_pct", 80.0, 0.0)

    body = (await client.get(f"/api/{aid}/performance_standards?from=2026-04-01&to=2026-04-01")).json()
    by_code = {x["route_code"]: x for x in body["rows"]}

    for code in ("RTHIN", "RQ", "UNPLANNED"):
        assert (by_code[code]["actual_value"], by_code[code]["meets"]) == (None, None), code
    assert by_code["RTHIN"]["not_judged"] == _not_judged(
        "too_few_planned_trips", floor=RANKING_MIN_SAMPLES, planned_trips=5
    )
    assert by_code["UNPLANNED"]["not_judged"] == _not_judged(
        "too_few_planned_trips", floor=RANKING_MIN_SAMPLES, planned_trips=0
    )
    assert by_code["RQ"]["not_judged"] == _not_judged(
        "too_few_observations", observations=10, floor=RANKING_MIN_SAMPLES
    )


async def test_a_service_delivered_target_is_not_judged_on_a_feed_that_reports_zero_delay(perf_client):
    from tests.fixtures.route_dist import seed_route_dist_day

    client, aid, pool = perf_client
    await seed_route_dist_day(pool, aid, "RD", _DELIVERED_DAY, [0] * 150)
    await _seed_standard(pool, aid, "RD", "service_delivered_pct", 80.0, 0.0)

    body = (await client.get(f"/api/{aid}/performance_standards?from=2026-04-01&to=2026-04-01")).json()

    assert body["rows"][0]["not_judged"] == _not_judged("zero_delay_feed")


async def test_a_service_delivered_target_is_not_judged_where_the_agencys_rate_is_not(perf_client):
    """A feed that has reported no trip not run would read every route 100%."""
    client, aid, pool = perf_client
    await pool.execute("DELETE FROM agg_service_delivered_daily WHERE agency_id = $1", aid)
    await _seed_standard(pool, aid, "RD", "service_delivered_pct", 80.0, 0.0)
    await _seed_standard(pool, aid, "RD", "vehicle_km_delivered_pct", 80.0, 0.0)

    body = (await client.get(f"/api/{aid}/performance_standards?from=2026-04-01&to=2026-04-01")).json()

    for row in body["rows"]:
        assert row["actual_value"] is None
        assert row["not_judged"] == _not_judged("feed_reports_no_cancellations"), row["metric_type"]


async def test_a_service_delivered_target_is_not_judged_on_a_day_whose_plan_by_route_was_not_saved(perf_client):
    client, aid, pool = perf_client
    await pool.execute(
        "INSERT INTO planned_trips_daily (agency_id, service_date, planned_trips) VALUES ($1, $2, 205)",
        aid,
        _DELIVERED_DAY,
    )
    await _seed_standard(pool, aid, "RD", "service_delivered_pct", 80.0, 0.0)

    body = (await client.get(f"/api/{aid}/performance_standards?from=2026-04-01&to=2026-04-01")).json()

    assert body["rows"][0]["not_judged"] == _not_judged("route_plan_unknown")


async def test_a_route_page_reads_its_service_delivered_target_alone(perf_client):
    client, aid, pool = perf_client
    await _seed_standard(pool, aid, "RD", "service_delivered_pct", 80.0, 0.0)
    await _seed_standard(pool, aid, "RQ", "service_delivered_pct", 80.0, 0.0)

    body = (await client.get(f"/api/{aid}/performance_standards?from=2026-04-01&to=2026-04-01&route=RD")).json()

    assert [(x["route_code"], x["actual_value"]) for x in body["rows"]] == [("RD", 80.0)]


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


# ── on-time targets ───────────────────────────────────────────────────────
#
# An on-time target is judged on the Routes table's own on-time figure for the
# route, over the request's scope and on-time definition.

_ON_TIME_DAY = "2026-05-14"
# 100 departures: on time for 60 of them under legacy_60s (any early departure
# counts), and for 80 under standard_m1_p5 (two minutes early is not on time,
# 200 s late is).
_ON_TIME_DELAYS = [0] * 40 + [200] * 40 + [-120] * 20


async def _standards(client, agency_id, query=""):
    resp = await client.get(f"/api/{agency_id}/performance_standards?from={_ON_TIME_DAY}&to={_ON_TIME_DAY}{query}")
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["agg", "live"])
@pytest.mark.parametrize(
    ("query", "preset", "early", "late", "share", "meets"),
    [
        ("", "standard_m1_p5", 60, 300, 80.0, True),
        ("&preset=legacy_60s", "legacy_60s", None, 60, 60.0, False),
        ("&early=any&late=60", "legacy_60s", None, 60, 60.0, False),
        ("&early=60&late=300", "standard_m1_p5", 60, 300, 80.0, True),
        ("&preset=standard_m1_p5", "standard_m1_p5", 60, 300, 80.0, True),
    ],
)
async def test_an_on_time_target_is_judged_on_the_tables_figure_under_the_requests_definition(
    reports_client,  # noqa: F811
    ch_client,
    ch_async_client,
    path,
    query,
    preset,
    early,
    late,
    share,
    meets,
):
    client, agency_id, pool = reports_client
    await _seed_route_at(pool, agency_id, "OT", "平日", _ON_TIME_DAY, "08:00", _ON_TIME_DELAYS)
    await _seed_standard(pool, agency_id, "OT", "on_time_pct", 70.0, 1000.0)
    band = await _load_for_path(path, agency_id, ch_client, ch_async_client)

    body = await _standards(client, agency_id, f"{band}{query}")
    table = (
        await client.get(f"/api/{agency_id}/reports/route_table?from={_ON_TIME_DAY}&to={_ON_TIME_DAY}{band}{query}")
    ).json()

    row = next(r for r in body["rows"] if r["route_code"] == "OT")
    assert row["metric_type"] == "on_time_pct"
    assert row["actual_value"] == float(table["rows"][0][2]) == share
    assert row["meets"] is meets
    assert row["not_judged"] is None
    assert row["achievement_rate"] == pytest.approx(share / 70.0)
    assert row["estimated_bonus_deduction"] == pytest.approx(1000.0 * (share - 70.0) / 70.0)
    definition = body["on_time_definition"]
    assert (definition["preset"], definition["early_tolerance_sec"], definition["late_tolerance_sec"]) == (
        preset,
        early,
        late,
    )
    assert definition["source"] == ("live" if path == "live" else "aggregates")
    # Stamped as the table's own definition is: its build, timetables and sources.
    assert definition["timetable_versions"] is not None
    for key in ("built_at", "timetable_versions", "source_mix"):
        assert definition[key] == table["definition"][key], key
    assert body["scope_applied"]["late"] is True
    assert body["scope_applied"]["early"] is True
    assert body["scope_applied"]["time_band"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["agg", "live"])
async def test_a_route_page_judges_its_own_route_alone(
    reports_client,  # noqa: F811
    ch_client,
    ch_async_client,
    path,
    monkeypatch,
):
    """A route's own page asks for that route: only its targets come back,
    and its figure is read from a table of that route alone, never the whole
    agency's, which under a time band is a live scan of every route."""
    from pipeline.reports import performance_standard

    client, agency_id, pool = reports_client
    await _seed_route_at(pool, agency_id, "OT", "平日", _ON_TIME_DAY, "08:00", _ON_TIME_DELAYS)
    await _seed_route_at(pool, agency_id, "OTHER", "平日", _ON_TIME_DAY, "08:00", [0] * 100)
    await _seed_standard(pool, agency_id, "OT", "on_time_pct", 70.0, 0.0)
    await _seed_standard(pool, agency_id, "OTHER", "on_time_pct", 70.0, 0.0)
    await _seed_standard(pool, agency_id, "OTHER", "ewt_sec", 50.0, 0.0)
    band = await _load_for_path(path, agency_id, ch_client, ch_async_client)

    scoped: list[tuple[str, ...]] = []
    original = performance_standard.compute_route_table

    async def spy(agency_id, ctx, *args, **kwargs):
        scoped.append(ctx.routes)
        return await original(agency_id, ctx, *args, **kwargs)

    monkeypatch.setattr(performance_standard, "compute_route_table", spy)
    body = await _standards(client, agency_id, f"{band}&routes=OT&route=OT")
    table = (
        await client.get(f"/api/{agency_id}/reports/route_table?from={_ON_TIME_DAY}&to={_ON_TIME_DAY}{band}")
    ).json()

    assert scoped == [("OT",)]
    assert [(r["route_code"], r["metric_type"]) for r in body["rows"]] == [("OT", "on_time_pct")]
    figure = next(float(r[2]) for r in table["rows"] if r[0] == "OT")
    assert body["rows"][0]["actual_value"] == figure == 80.0


@pytest.mark.asyncio
async def test_an_on_time_target_follows_the_scopes_days_but_not_its_route_filter(reports_client):  # noqa: F811
    """The day filter narrows the figure the target is judged on, as it does
    the table's; the route filter never hides a configured route's figure."""
    from tests.fixtures.route_dist import seed_route_dist_day

    client, agency_id, pool = reports_client
    await seed_route_dist_day(pool, agency_id, "OT", date.fromisoformat(_ON_TIME_DAY), _ON_TIME_DELAYS)
    await _seed_standard(pool, agency_id, "OT", "on_time_pct", 70.0, 0.0)

    filtered = await _standards(client, agency_id, "&routes=ELSEWHERE")
    assert filtered["rows"][0]["actual_value"] == 80.0
    # 2026-05-14 is a Thursday.
    weekend = await _standards(client, agency_id, "&dow=weekend")
    assert weekend["rows"][0]["actual_value"] is None
    assert weekend["rows"][0]["meets"] is None
    assert weekend["scope_applied"]["dow"] is True
    assert weekend["scope_applied"]["routes"] is False


@pytest.mark.asyncio
async def test_an_on_time_target_is_not_judged_on_thin_data_or_a_feed_that_reports_no_delay(reports_client):  # noqa: F811
    """A route the table leaves out for too few observations, or for a feed
    that always reads zero delay, has no figure to judge: a zero-delay feed
    would read 100% on time and meet any target."""
    from pipeline.reports.rankings import RANKING_MIN_SAMPLES
    from tests.fixtures.observed_route import seed_observed_route
    from tests.fixtures.route_dist import seed_route_dist_day

    client, agency_id, pool = reports_client
    await seed_route_dist_day(pool, agency_id, "THIN", date(2026, 10, 1), [0] * (RANKING_MIN_SAMPLES - 1))
    await seed_observed_route(pool, agency_id, "ZERO", "2026-09-24", "2026-10-23", delay=0)
    await seed_observed_route(pool, agency_id, "REAL", "2026-09-24", "2026-10-23", delay=30)
    for code in ("THIN", "ZERO", "REAL"):
        await _seed_standard(pool, agency_id, code, "on_time_pct", 50.0, 0.0)

    await _seed_standard(pool, agency_id, "UNSEEN", "on_time_pct", 50.0, 0.0)

    resp = await client.get(f"/api/{agency_id}/performance_standards?from=2026-09-24&to=2026-10-23")
    by_code = {r["route_code"]: r for r in resp.json()["rows"]}
    for code in ("THIN", "ZERO", "UNSEEN"):
        assert (by_code[code]["actual_value"], by_code[code]["meets"]) == (None, None), code
    # Each says why it is not judged, so a figure shown elsewhere on the page
    # never meets an unexplained blank here.
    assert by_code["THIN"]["not_judged"] == _not_judged(
        "too_few_observations", observations=RANKING_MIN_SAMPLES - 1, floor=RANKING_MIN_SAMPLES
    )
    assert by_code["UNSEEN"]["not_judged"] == _not_judged(
        "too_few_observations", observations=0, floor=RANKING_MIN_SAMPLES
    )
    assert by_code["ZERO"]["not_judged"] == _not_judged("zero_delay_feed")
    assert by_code["REAL"]["actual_value"] == 100.0
    assert by_code["REAL"]["meets"] is True
    assert by_code["REAL"]["not_judged"] is None


@pytest.mark.asyncio
async def test_a_route_page_counts_its_own_thin_route_alone(reports_client, monkeypatch):  # noqa: F811
    """Under the floor, the route's count is read for that route alone too."""
    from pipeline.reports import performance_standard
    from pipeline.reports.rankings import RANKING_MIN_SAMPLES
    from tests.fixtures.route_dist import seed_route_dist_day

    client, agency_id, pool = reports_client
    day = date.fromisoformat(_ON_TIME_DAY)
    await seed_route_dist_day(pool, agency_id, "THIN", day, [30] * 42)
    await seed_route_dist_day(pool, agency_id, "BUSY", day, [30] * 500)
    await _seed_standard(pool, agency_id, "THIN", "on_time_pct", 50.0, 0.0)

    scoped: list[tuple[str, ...]] = []
    original = performance_standard.compute_route_table

    async def spy(agency_id, ctx, *args, **kwargs):
        scoped.append(ctx.routes)
        return await original(agency_id, ctx, *args, **kwargs)

    monkeypatch.setattr(performance_standard, "compute_route_table", spy)
    body = await _standards(client, agency_id, "&route=THIN")

    assert scoped and all(routes == ("THIN",) for routes in scoped)
    assert body["rows"][0]["not_judged"] == _not_judged(
        "too_few_observations", observations=42, floor=RANKING_MIN_SAMPLES
    )


@pytest.mark.asyncio
async def test_without_an_on_time_target_the_response_reads_dates_only(reports_client):  # noqa: F811
    client, agency_id, pool = reports_client
    await _seed_standard(pool, agency_id, "R1", "ewt_sec", 50.0, 0.0)

    body = await _standards(client, agency_id, "&late=300")
    assert body["on_time_definition"] is None
    assert [k for k, v in body["scope_applied"].items() if v] == ["from", "to"]
    assert body["rows"][0]["meets"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["&preset=nope", "&preset=standard_m1_p5&late=300", "&preset=legacy_60s&early=60"])
async def test_an_unusable_on_time_definition_is_refused(reports_client, query):  # noqa: F811
    client, agency_id, _ = reports_client
    resp = await client.get(f"/api/{agency_id}/performance_standards?from={_ON_TIME_DAY}&to={_ON_TIME_DAY}{query}")
    assert resp.status_code == 400
