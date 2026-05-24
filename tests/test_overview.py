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
