"""Tests for the 概況 (Overview) tab endpoint."""

import pytest
from datetime import date, datetime, time, timedelta, timezone

from api.range import RangeCtx


@pytest.mark.asyncio
async def test_overview_endpoint_returns_empty_payload_when_no_data(client, aagency_id):
    """An agency with no `updates` rows in range returns a zero-filled
    OverviewSummary, not a 404 or 500."""
    r = await client.get(f"/api/{aagency_id}/overview/summary?from=2020-01-01&to=2020-01-07")
    assert r.status_code == 200
    body = r.json()
    assert body["headline"]["avg_min"] is None
    assert body["headline"]["baseline_avg_min"] is None
    assert body["movers"]["worse"] == []
    assert body["movers"]["better"] == []
    assert body["concentration"]["top_routes"] == []
    assert body["peak_hour"] is None
    assert body["service_split"] == {}
    assert body["sparkline_points"] == []


@pytest.mark.asyncio
async def test_headline_avg_and_samples_from_seeded_rows(aconn, aagency_id):
    """Three observations inside the range -> avg_min reflects them."""
    base = datetime.combine(date(2026, 5, 18), time(12, 0), tzinfo=timezone.utc)
    rows = [
        ("pb1", base, 60),                          # 1.0 min
        ("pb2", base + timedelta(hours=1), 180),    # 3.0 min
        ("pb3", base + timedelta(hours=2), 300),    # 5.0 min
    ]
    for fname, cap, dep in rows:
        await aconn.execute(
            "INSERT INTO updates "
            "(agency_id, file_name, captured_at, trip_id, service_type, "
            " scheduled_time, route_code, stop_sequence, dep_delay) "
            "VALUES ($1, $2, $3, 'trip_x', '平日', '10:00', 'R1', 1, $4)",
            aagency_id, fname, cap, dep,
        )

    from pipeline.reports import compute_overview_summary

    ctx = RangeCtx(from_date=date(2026, 5, 18), to_date=date(2026, 5, 24))
    out = await compute_overview_summary(aagency_id, ctx, aconn, "ja")
    h = out["headline"]
    assert h["samples"] == 3
    assert h["avg_min"] == pytest.approx(3.0, abs=0.01)


@pytest.mark.asyncio
async def test_baseline_is_shifted_one_week_back(aconn, aagency_id):
    """This-week avg = 4.0 min; baseline-week avg = 2.0 min ->
    delta = +2.0 min, +100%."""
    this_week = datetime.combine(date(2026, 5, 18), time(12, 0), tzinfo=timezone.utc)
    last_week = this_week - timedelta(days=7)
    # last week: two rows averaging 2 min
    for i, dep in enumerate([60, 180]):
        await aconn.execute(
            "INSERT INTO updates "
            "(agency_id, file_name, captured_at, trip_id, service_type, "
            " scheduled_time, route_code, stop_sequence, dep_delay) "
            "VALUES ($1, $2, $3, 'trip_p', '平日', '10:00', 'R1', 1, $4)",
            aagency_id, f"pb_prev_{i}", last_week + timedelta(hours=i), dep,
        )
    # this week: two rows averaging 4 min
    for i, dep in enumerate([180, 300]):
        await aconn.execute(
            "INSERT INTO updates "
            "(agency_id, file_name, captured_at, trip_id, service_type, "
            " scheduled_time, route_code, stop_sequence, dep_delay) "
            "VALUES ($1, $2, $3, 'trip_t', '平日', '10:00', 'R1', 1, $4)",
            aagency_id, f"pb_cur_{i}", this_week + timedelta(hours=i), dep,
        )

    from pipeline.reports import compute_overview_summary

    ctx = RangeCtx(from_date=date(2026, 5, 18), to_date=date(2026, 5, 24))
    out = await compute_overview_summary(aagency_id, ctx, aconn, "ja")
    h = out["headline"]
    assert h["avg_min"] == pytest.approx(4.0, abs=0.01)
    assert h["baseline_avg_min"] == pytest.approx(2.0, abs=0.01)
    assert h["delta_min"] == pytest.approx(2.0, abs=0.01)
    assert h["delta_pct"] == pytest.approx(100.0, abs=0.5)


@pytest.mark.asyncio
async def test_baseline_missing_returns_null_delta(aconn, aagency_id):
    """No data in the prior-week window -> delta fields are None."""
    cur = datetime.combine(date(2026, 5, 18), time(12, 0), tzinfo=timezone.utc)
    await aconn.execute(
        "INSERT INTO updates "
        "(agency_id, file_name, captured_at, trip_id, service_type, "
        " scheduled_time, route_code, stop_sequence, dep_delay) "
        "VALUES ($1, $2, $3, 'trip_x', '平日', '10:00', 'R1', 1, 120)",
        aagency_id, "pb_cur", cur,
    )

    from pipeline.reports import compute_overview_summary

    ctx = RangeCtx(from_date=date(2026, 5, 18), to_date=date(2026, 5, 24))
    out = await compute_overview_summary(aagency_id, ctx, aconn, "ja")
    h = out["headline"]
    assert h["avg_min"] is not None
    assert h["baseline_avg_min"] is None
    assert h["delta_min"] is None
    assert h["delta_pct"] is None


@pytest.mark.asyncio
async def test_movers_ranks_top_3_worse_and_top_3_better(aconn, aagency_id):
    """Five routes with varied this-week vs prior-week deltas;
    top 3 worsened + top 3 improved come out sorted by |delta_min|."""
    cur = datetime.combine(date(2026, 5, 18), time(12, 0), tzinfo=timezone.utc)
    prv = cur - timedelta(days=7)
    # (route, prior_avg_sec, current_avg_sec)
    routes = [
        ("R_A", 60, 600),   # +9 min (worst)
        ("R_B", 60, 480),   # +7 min
        ("R_C", 60, 360),   # +5 min
        ("R_D", 60, 120),   # +1 min (still worse but not top-3)
        ("R_E", 600, 60),   # -9 min (best improvement)
        ("R_F", 480, 60),   # -7 min
        ("R_G", 360, 60),   # -5 min
        ("R_H", 120, 60),   # -1 min
    ]
    rows_to_insert = []
    for code, prior_dep, cur_dep in routes:
        rows_to_insert.append(("rs_prv_" + code, prv, prior_dep, code))
        rows_to_insert.append(("rs_cur_" + code, cur, cur_dep, code))
    for fname, when, dep, code in rows_to_insert:
        await aconn.execute(
            "INSERT INTO updates "
            "(agency_id, file_name, captured_at, trip_id, service_type, "
            " scheduled_time, route_code, stop_sequence, dep_delay) "
            "VALUES ($1, $2, $3, 'trip_' || $4, '平日', '10:00', $4, 1, $5)",
            aagency_id, fname, when, code, dep,
        )

    from pipeline.reports import compute_overview_summary

    ctx = RangeCtx(from_date=date(2026, 5, 18), to_date=date(2026, 5, 24))
    out = await compute_overview_summary(aagency_id, ctx, aconn, "ja")
    worse_codes = [m["route_code"] for m in out["movers"]["worse"]]
    better_codes = [m["route_code"] for m in out["movers"]["better"]]
    assert worse_codes == ["R_A", "R_B", "R_C"]
    assert better_codes == ["R_E", "R_F", "R_G"]
