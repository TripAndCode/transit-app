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
