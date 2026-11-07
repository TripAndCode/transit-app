"""API tests for GET /api/{agency_id}/performance_standards.

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
        ("", "legacy_60s", None, 60, 60.0, False),
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
    assert row["metric_scope"] == "route"
    assert row["actual_value"] == float(table["rows"][0][2]) == share
    assert row["meets"] is meets
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
    assert body["rows"][0]["actual_value"] == figure == 60.0


@pytest.mark.asyncio
async def test_an_on_time_target_follows_the_scopes_days_but_not_its_route_filter(reports_client):  # noqa: F811
    """The day filter narrows the figure the target is judged on, as it does
    the table's; the route filter never hides a configured route's figure."""
    from tests.fixtures.route_dist import seed_route_dist_day

    client, agency_id, pool = reports_client
    await seed_route_dist_day(pool, agency_id, "OT", date.fromisoformat(_ON_TIME_DAY), _ON_TIME_DELAYS)
    await _seed_standard(pool, agency_id, "OT", "on_time_pct", 70.0, 0.0)

    filtered = await _standards(client, agency_id, "&routes=ELSEWHERE")
    assert filtered["rows"][0]["actual_value"] == 60.0
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

    resp = await client.get(f"/api/{agency_id}/performance_standards?from=2026-09-24&to=2026-10-23")
    by_code = {r["route_code"]: r for r in resp.json()["rows"]}
    assert by_code["THIN"]["actual_value"] is None
    assert by_code["ZERO"]["actual_value"] is None
    assert by_code["ZERO"]["meets"] is None
    assert by_code["REAL"]["actual_value"] == 100.0
    assert by_code["REAL"]["meets"] is True


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
