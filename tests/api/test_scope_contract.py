"""Every lens endpoint declares the scope it honoured (spec §2)."""

import pytest

from api.scope_applied import SCOPE_FIELDS
from tests.api.test_reports import reports_app, reports_client  # noqa: F401 -- fixtures


def _assert_declares_scope(body: dict) -> None:
    applied = body["scope_applied"]
    assert set(applied) == set(SCOPE_FIELDS)
    assert all(isinstance(v, bool) for v in applied.values())
    assert applied["hour"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/api/{a}/reports/ranking",
        "/api/{a}/reports/on_time",
        "/api/{a}/reports/trend",
        "/api/{a}/reports/compare_ranking",
        "/api/{a}/reports/dow_weekday",
        "/api/{a}/reports/dwell_run",
        "/api/{a}/overview/summary",
        "/api/{a}/peak-hour-breakdown?hour=8",
        "/api/{a}/headway_quality",
        "/api/{a}/performance_standards",
        "/api/{a}/weather_delay",
        "/api/network/summary",
        "/api/{a}/forecast/overview",
        "/api/{a}/forecast/heatmap?route=R1",
    ],
)
async def test_lens_endpoint_declares_scope_applied(reports_client, path):  # noqa: F811
    client, agency_id, _ = reports_client
    resp = await client.get(path.format(a=agency_id))
    assert resp.status_code == 200, resp.text
    _assert_declares_scope(resp.json())


@pytest.mark.asyncio
async def test_endpoints_that_ignore_a_field_say_so(reports_client):  # noqa: F811
    client, agency_id, _ = reports_client
    headway = (await client.get(f"/api/{agency_id}/headway_quality")).json()["scope_applied"]
    assert headway["service"] is False and headway["time_band"] is False
    network = (await client.get("/api/network/summary")).json()["scope_applied"]
    assert network["routes"] is False and network["from"] is True
