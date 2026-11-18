"""API tests for the route report card: GET /api/{agency_id}/routes/{route}/report-card
and GET /api/{agency_id}/report-cards.

Seeds the Postgres aggregates the card reads directly, one planned day of a
closed month: the Routes table's ``agg_route_daily_dist``, the trips observed
in ``agg_route_daily``, the saved plan split by route, the trips reported not
run, and each high-frequency route's daily headways.
"""

import csv
import io
import json
from datetime import date

import httpx
import pytest
from httpx import ASGITransport

from pipeline.cache import clear_all
from tests.conftest import _test_pool, confirm_rt_field_coverage
from tests.fixtures.route_dist import seed_route_dist_day
from tests.fixtures.users import seed_session

DAY = date(2026, 6, 1)
MONTH = "2026-06"
TRIPS = 1000

# 2000 stop events per route: (on time, early by two minutes, late by seven).
_DELAYS = {
    # Low-frequency, 85.0% on time and 5.0% early: B and C, so C.
    "LF": (1700, 100, 200),
    # 95.0% on time, 0.5% early, but 7% of its trips reported not run: capped at D.
    "TRUANT": (1900, 10, 90),
    # High-frequency: graded on its headways, not these.
    "HF": (1900, 10, 90),
    "OLDHF": (1900, 10, 90),
    # Half its planned trips observed.
    "LOWCOV": (1900, 10, 90),
}

# A day's scheduled headways: median, high-frequency, sum, sum of squares;
# ten even 8-minute gaps, a scheduled mean wait of 240 s.
_EVERY_8_MIN = (480.0, True, 4800.0, 2304000.0)


def _delays(on_time: int, early: int, late: int) -> list[int]:
    return [30] * on_time + [-120] * early + [420] * late


@pytest.fixture
async def card_client(apply_schema):
    from api.main import app

    clear_all()
    pool = await _test_pool()
    app.state.pool = pool
    aid = await pool.fetchval(
        "INSERT INTO agencies (agency_name, feed_url, ingest_strategy) VALUES ($1, $2, 'static_join') "
        "RETURNING agency_id",
        "Report Card Test Agency",
        "http://report-card-test.example.com",
    )
    await confirm_rt_field_coverage(pool, aid)
    for route, counts in _DELAYS.items():
        await seed_route_dist_day(pool, aid, route, DAY, _delays(*counts))
    await seed_route_dist_day(pool, aid, "THIN", DAY, [30] * 40 + [420] * 10)
    observed = {route: (TRIPS, 2000) for route in _DELAYS} | {"LOWCOV": (TRIPS // 2, 2000), "THIN": (25, 50)}
    await pool.executemany(
        "INSERT INTO agg_route_daily (agency_id, date, route_code, service_type, avg_delay_sec, worst_delay_sec, "
        "trips_observed, samples, last_seen_at) VALUES ($1, $2, $3, '', 30, 30, $4, $5, NOW())",
        [(aid, DAY, route, trips, samples) for route, (trips, samples) in observed.items()],
    )
    # "=SUM(1)" is a feed's route code that opens like a spreadsheet formula,
    # planned but never observed.
    by_route = {route: TRIPS for route in _DELAYS} | {"THIN": 25, "=SUM(1)": 5}
    await pool.execute(
        "INSERT INTO planned_trips_daily (agency_id, service_date, planned_trips, planned_trips_by_route) "
        "VALUES ($1, $2, $3, $4::jsonb)",
        aid,
        DAY,
        sum(by_route.values()),
        json.dumps(by_route),
    )
    await pool.executemany(
        "INSERT INTO agg_service_delivered_daily (agency_id, date, route_code, non_executed_trips) "
        "VALUES ($1, $2, $3, $4)",
        [(aid, DAY, "TRUANT", 70), (aid, DAY, "HF", 10)],
    )
    # HF: ten gaps, one of them under a quarter of 480 s, whose mean wait is
    # 264 s: 24 s over the scheduled 240 s, an excess wait of 0.4 min (A),
    # and 10.0% bunched (B). OLDHF's day was built before bunching was counted.
    await pool.executemany(
        "INSERT INTO agg_route_headway_daily (agency_id, route_code, date, actual_headway_median_sec, "
        " actual_samples, actual_headway_sum_sec, actual_headway_sumsq_sec2, long_gap_count, "
        " scheduled_headway_median_sec, is_high_frequency, scheduled_headway_sum_sec, "
        " scheduled_headway_sumsq_sec2, bunched_count, bunching_judged) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)",
        [
            (aid, "HF", DAY, 480.0, 10, 4800.0, 2534400.0, 0, *_EVERY_8_MIN, 1, 10),
            (aid, "OLDHF", DAY, 480.0, 10, 4800.0, 2534400.0, 0, *_EVERY_8_MIN, None, None),
        ],
    )
    async with pool.acquire() as conn:
        admin = await seed_session(conn, role="admin")
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", cookies={"sid": admin.sid}
    ) as client:
        yield client, aid, pool
    async with pool.acquire() as conn:
        await conn.execute(
            "TRUNCATE users, agencies, agg_route_daily_dist, agg_route_daily, planned_trips_daily, "
            "agg_service_delivered_daily, agg_route_headway_daily CASCADE"
        )
    await pool.close()
    clear_all()


async def _cards(client, aid, query=f"month={MONTH}"):
    r = await client.get(f"/api/{aid}/report-cards?{query}")
    assert r.status_code == 200, r.text
    return r.json(), {card["route_code"]: card for card in r.json()["cards"]}


def _figure(card, metric):
    return next(s for s in card["subscores"] if s["metric"] == metric)


async def test_a_low_frequency_route_takes_the_worse_of_on_time_and_early_departures(card_client):
    client, aid, _pool = card_client
    _body, cards = await _cards(client, aid)
    lf = cards["LF"]
    assert lf["frequency"] == "low"
    assert (_figure(lf, "on_time_pct")["value"], _figure(lf, "on_time_pct")["letter"]) == (85.0, "B")
    assert (_figure(lf, "early_pct")["value"], _figure(lf, "early_pct")["letter"]) == (5.0, "C")
    assert lf["grade"] == "C"
    assert lf["raise_to"] == [{"metric": "early_pct", "value": 5.0, "target": 2.0}]
    # The interval counts the route's trips, not its stop events.
    low, high = _figure(lf, "on_time_pct")["ci_low"], _figure(lf, "on_time_pct")["ci_high"]
    assert low < 85.0 < high and high - low > 4.0


async def test_a_high_frequency_route_is_graded_on_its_wait_and_bunching(card_client):
    client, aid, _pool = card_client
    _body, cards = await _cards(client, aid)
    hf = cards["HF"]
    assert hf["frequency"] == "high"
    assert _figure(hf, "ewt_min")["value"] == pytest.approx(0.4)
    assert _figure(hf, "ewt_min")["ci_low"] is None
    # Bunching pools fewer days than the month's trips cover, so it claims no interval.
    assert (_figure(hf, "bunching_pct")["ci_low"], _figure(hf, "bunching_pct")["ci_high"]) == (None, None)
    assert (_figure(hf, "bunching_pct")["value"], _figure(hf, "bunching_pct")["letter"]) == (10.0, "B")
    assert hf["grade"] == "B"
    assert hf["raise_to"] == [{"metric": "bunching_pct", "value": 10.0, "target": 5.0}]
    assert _figure(hf, "on_time_pct")["graded"] is False


async def test_truancy_caps_an_otherwise_a_route_at_d(card_client):
    client, aid, _pool = card_client
    _body, cards = await _cards(client, aid)
    truant = cards["TRUANT"]
    assert (_figure(truant, "on_time_pct")["letter"], _figure(truant, "early_pct")["letter"]) == ("A", "A")
    assert _figure(truant, "service_delivered_pct")["value"] == 93.0
    assert truant["grade"] == "D"
    assert truant["raise_to"] == [{"metric": "service_delivered_pct", "value": 93.0, "target": 95.0}]


async def test_a_route_failing_the_gate_shows_no_grade_and_says_why(card_client):
    client, aid, _pool = card_client
    _body, cards = await _cards(client, aid)
    low = cards["LOWCOV"]
    assert low["grade"] is None
    assert low["not_graded"] == "coverage_low"
    assert low["coverage_pct"] == 50.0
    assert low["raise_to"] == []
    thin = cards["THIN"]
    assert (thin["grade"], thin["not_graded"], thin["observations"], thin["floor"]) == (
        None,
        "too_few_observations",
        50,
        100,
    )


async def test_unmeasured_bunching_is_not_judged_never_read_as_zero(card_client):
    client, aid, _pool = card_client
    _body, cards = await _cards(client, aid)
    old = cards["OLDHF"]
    assert old["grade"] is None
    assert old["not_graded"] == "not_judged"
    assert old["missing"] == [{"metric": "bunching_pct", "reason": "not_measured"}]
    assert _figure(old, "bunching_pct")["value"] is None


async def test_a_feed_that_reports_no_trip_not_run_is_not_judged(card_client):
    client, aid, pool = card_client
    await pool.execute("DELETE FROM agg_service_delivered_daily WHERE agency_id = $1", aid)
    _body, cards = await _cards(client, aid)
    lf = cards["LF"]
    assert lf["grade"] is None
    assert lf["missing"] == [{"metric": "service_delivered_pct", "reason": "feed_reports_no_cancellations"}]


async def test_a_routes_card_matches_the_list_and_carries_three_months(card_client):
    client, aid, _pool = card_client
    _body, cards = await _cards(client, aid)
    r = await client.get(f"/api/{aid}/routes/HF/report-card?month={MONTH}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["card"] == cards["HF"]
    assert [m["month"] for m in body["history"]] == ["2026-04", "2026-05", "2026-06"]
    assert [m["grade"] for m in body["history"]] == [None, None, "B"]
    assert body["history"][0]["not_graded"] == "no_data"
    assert body["definition"]["value_basis"] == "feed_prediction"
    assert body["definition"]["preset"] == "standard_m1_p5"
    assert body["rules"]["bunching_pct_edges"] == [5.0, 10.0, 15.0, 20.0]
    # Its own month, none of the shared scope's conditions.
    assert not any(body["scope_applied"].values())


async def test_a_route_with_nothing_that_month_has_no_card(card_client):
    client, aid, _pool = card_client
    r = await client.get(f"/api/{aid}/routes/NOWHERE/report-card?month={MONTH}")
    assert r.status_code == 200
    assert r.json()["card"] is None


async def test_a_month_not_yet_closed_is_refused(card_client):
    client, aid, _pool = card_client
    r = await client.get(f"/api/{aid}/report-cards?month=2999-01")
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "month_not_closed"
    assert (await client.get(f"/api/{aid}/report-cards?month=2026-13")).status_code == 422


@pytest.mark.parametrize("month", ["0000-01", "0000-12", "0001-01", "0001-02", "1899-12"])
async def test_a_year_no_calendar_date_can_hold_is_refused_not_a_server_error(card_client, month):
    client, aid, _pool = card_client
    for path in (f"/api/{aid}/report-cards?month={month}", f"/api/{aid}/routes/HF/report-card?month={month}"):
        assert (await client.get(path)).status_code == 422, path


async def test_the_list_downloads_as_csv_with_the_reports_preamble(card_client):
    client, aid, _pool = card_client
    r = await client.get(f"/api/{aid}/report-cards?month={MONTH}&format=csv&lang=ja")
    assert r.status_code == 200
    rows = list(csv.reader(io.StringIO(r.text.lstrip("﻿"))))
    assert "値の根拠=" in rows[0][0] and "算出方法の説明=" in rows[0][0] and "算出方法=" in rows[0][0]
    assert rows[1][0].startswith("評価基準")
    header = next(i for i, row in enumerate(rows) if row and row[0] == "系統コード")
    by_route = {row[0]: row for row in rows[header + 1 :]}
    assert by_route["HF"][3] == "B"
    # A feed-written code that opens like a formula is written as text.
    assert "'=SUM(1)" in by_route and "=SUM(1)" not in by_route
    assert (by_route["LOWCOV"][3], by_route["LOWCOV"][4]) == ("", "coverage_low")


async def test_only_an_admin_reads_a_grade(card_client):
    """A grade waits on the publication process: a signed-in user who is not
    an admin, or no one, gets none."""
    from api.main import app

    _client, aid, pool = card_client
    async with pool.acquire() as conn:
        user = await seed_session(conn, role="user")
    for cookies in ({"sid": user.sid}, {}):
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test", cookies=cookies) as c:
            for path in (f"/api/{aid}/report-cards?month={MONTH}", f"/api/{aid}/routes/HF/report-card?month={MONTH}"):
                assert (await c.get(path)).status_code in (401, 403)
