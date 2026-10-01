"""The scope controls' summary: one read of agg_route_daily_dist per section."""

from datetime import date, timedelta

import pytest

from api.range import RangeCtx
from pipeline.reports.scope_summary import compute_scope_summary

N_BUCKETS = 37


def _hist(bucket: int, n: int) -> list[int]:
    h = [0] * N_BUCKETS
    h[bucket] = n
    return h


async def _seed(aconn, agency_id, route_code, day, samples, sum_delay_sec, hist, service="平日"):
    await aconn.execute(
        "INSERT INTO agg_route_daily_dist (agency_id, date, route_code, service_type, "
        "samples, sum_delay_sec, on_time_count, late5_count, hist) VALUES ($1,$2,$3,$4,$5,$6,0,0,$7)",
        agency_id,
        day,
        route_code,
        service,
        samples,
        sum_delay_sec,
        hist,
    )


@pytest.fixture
async def seeded(aconn, aagency_id):
    # R1: every day 9/1..9/28 at 1.0 min, all of it in the [60, 120) bucket.
    for i in range(28):
        await _seed(aconn, aagency_id, "R1", date(2026, 9, 1) + timedelta(days=i), 100, 6000, _hist(7, 100))
    # R2: one Saturday at 5.0 min, in the [300, 360) bucket.
    await _seed(aconn, aagency_id, "R2", date(2026, 9, 26), 50, 15000, _hist(11, 50))
    return aconn, aagency_id


def _ctx(**kw) -> RangeCtx:
    return RangeCtx(from_date=date(2026, 9, 20), to_date=date(2026, 9, 28), **kw)


@pytest.mark.asyncio
async def test_spans_the_agencys_data(seeded):
    conn, agency_id = seeded
    body = await compute_scope_summary(agency_id, _ctx(), conn)
    assert body["earliest"] == date(2026, 9, 1)
    assert body["latest"] == date(2026, 9, 28)


@pytest.mark.asyncio
async def test_days_ignore_the_period_and_the_weekday_filter(seeded):
    conn, agency_id = seeded
    body = await compute_scope_summary(agency_id, _ctx(dow="weekday"), conn)
    days = {d["date"]: d for d in body["days"]}
    assert len(days) == 28
    assert days[date(2026, 9, 1)]["avg_min"] == 1.0
    assert days[date(2026, 9, 26)] == {"date": date(2026, 9, 26), "avg_min": 2.33, "samples": 150}


@pytest.mark.asyncio
async def test_days_cover_at_most_the_last_ninety_days(aconn, aagency_id):
    for i in range(120):
        await _seed(aconn, aagency_id, "R1", date(2026, 6, 1) + timedelta(days=i), 10, 600, _hist(7, 10))
    body = await compute_scope_summary(aagency_id, _ctx(), aconn)
    assert len(body["days"]) == 90
    assert body["days"][-1]["date"] == date(2026, 9, 28)
    assert body["earliest"] == date(2026, 6, 1)


@pytest.mark.asyncio
async def test_weekdays_cover_the_period_and_ignore_the_weekday_filter(seeded):
    conn, agency_id = seeded
    body = await compute_scope_summary(agency_id, _ctx(dow="weekday"), conn)
    weekdays = {w["dow"]: w for w in body["weekdays"]}
    assert weekdays["sat"] == {"dow": "sat", "avg_min": 2.33, "samples": 150}
    assert weekdays["mon"]["avg_min"] == 1.0
    assert weekdays["mon"]["samples"] == 200  # 9/21 and 9/28


@pytest.mark.asyncio
async def test_routes_cover_the_period_and_ignore_the_routes_filter(seeded):
    conn, agency_id = seeded
    body = await compute_scope_summary(agency_id, _ctx(routes=("R1",)), conn)
    routes = {r["route_code"]: r for r in body["routes"]}
    assert routes["R1"]["avg_min"] == 1.0
    assert routes["R2"] == {"route_code": "R2", "avg_min": 5.0, "samples": 50}


@pytest.mark.asyncio
async def test_tolerance_shares_follow_the_whole_scope(seeded):
    conn, agency_id = seeded
    body = await compute_scope_summary(agency_id, _ctx(dow="weekday"), conn)
    shares = {t["late_sec"]: t["on_time_pct"] for t in body["tolerance"]}
    assert sorted(shares) == list(range(0, 601, 60))
    assert shares[0] == 0.0
    assert shares[60] == 1.7
    assert shares[120] == 100.0


@pytest.mark.asyncio
async def test_tolerance_honours_an_early_bound(seeded):
    conn, agency_id = seeded
    body = await compute_scope_summary(agency_id, _ctx(), conn, early_sec=0)
    # Every delay seeded is late, so an early bound of zero changes nothing.
    assert {t["late_sec"]: t["on_time_pct"] for t in body["tolerance"]}[600] == 100.0


@pytest.mark.asyncio
async def test_an_agency_without_aggregates_answers_empty(aconn, aagency_id):
    body = await compute_scope_summary(aagency_id, _ctx(), aconn)
    assert body == {"earliest": None, "latest": None, "days": [], "weekdays": [], "routes": [], "tolerance": []}


@pytest.mark.asyncio
async def test_the_endpoint_answers_with_the_summary_and_its_scope(client, seeded):
    _, agency_id = seeded
    resp = await client.get(f"/api/{agency_id}/scope/summary?from=2026-09-20&to=2026-09-28&dow=weekday")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["earliest"] == "2026-09-01"
    assert body["latest"] == "2026-09-28"
    assert len(body["days"]) == 28
    assert {w["dow"] for w in body["weekdays"]} >= {"sat", "mon"}
    assert body["tolerance"][2] == {"late_sec": 120, "on_time_pct": 100.0}
    assert body["ctx"]["from"] == "2026-09-20"
    applied = body["scope_applied"]
    assert applied["from"] is True and applied["routes"] is True
    assert applied["time_band"] is False and applied["hour"] is False and applied["late"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(("early", "status"), [(0, 200), (3600, 200), (3601, 422), (-1, 422)])
async def test_the_endpoint_bounds_the_early_tolerance(client, seeded, early, status):
    _, agency_id = seeded
    resp = await client.get(f"/api/{agency_id}/scope/summary?from=2026-09-20&to=2026-09-28&early={early}")
    assert resp.status_code == status, resp.text


@pytest.mark.asyncio
async def test_the_endpoint_404s_an_unknown_agency(client, aconn):
    resp = await client.get("/api/999999/scope/summary")
    assert resp.status_code == 404
