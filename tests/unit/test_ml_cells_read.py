"""The forecast overlay's read. The agency overview pools forecast rows by each
route-hour's past observations, so only it joins that history; a single
route's heatmap weights by its own baseline cells and reads the forecast alone."""

from datetime import date
from unittest.mock import AsyncMock

import pytest

import api.routers.reports as reports_mod

_ROW = {"service_date": date(2026, 10, 30), "hour": 18, "mean_min": 10.0, "q10": 3.0, "q90": 17.2}


@pytest.fixture(autouse=True)
def _ml_on(monkeypatch):
    async def on(_name):
        return True

    monkeypatch.setattr(reports_mod, "aflag", on)


def _conn(rows):
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=rows)
    return conn


async def test_a_route_read_takes_the_forecast_alone_without_the_history_join():
    conn = _conn([_ROW])

    cells = await reports_mod._fetch_ml_cells(conn, 8, "360868713")

    sql, *args = conn.fetch.await_args.args
    assert "agg_route_hour_dow" not in sql
    assert args == [8, "360868713"]
    assert "samples" not in cells[0] and "sum_delay_sec" not in cells[0]
    assert (cells[0]["dow"], cells[0]["hour"], cells[0]["q10"], cells[0]["q90"]) == (5, 18, 3.0, 17.2)


async def test_the_agency_read_joins_each_route_hours_history_as_its_weight():
    conn = _conn(
        [
            {**_ROW, "past_samples": 300, "past_sum_delay_sec": 18000},
            {**_ROW, "past_samples": None, "past_sum_delay_sec": None},
        ]
    )

    cells = await reports_mod._fetch_ml_cells(conn, 8, None)

    sql, *args = conn.fetch.await_args.args
    assert "agg_route_hour_dow" in sql
    assert args == [8]
    assert (cells[0]["samples"], cells[0]["sum_delay_sec"]) == (300, 18000)
    # A route-hour with no history weighs nothing: 0 observations, no delay total.
    assert (cells[1]["samples"], cells[1]["sum_delay_sec"]) == (0, None)
