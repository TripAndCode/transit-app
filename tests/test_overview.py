"""Tests for the 概況 (Overview) tab endpoint."""

import pytest


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
