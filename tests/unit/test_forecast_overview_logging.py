"""Unit test for forecast_overview's swallowed-failure logging (finding D2).

The recent-daily sparkline fetch is purely decorative (see the endpoint's own
docstring comment), so a failure there must still degrade to an empty
sparkline list rather than 500ing the whole response -- but the failure has
to be logged, not silently dropped.
"""

import logging
from unittest.mock import AsyncMock

import api.routers.reports as reports_mod


async def test_forecast_overview_logs_recent_daily_fetch_failure(caplog, monkeypatch):
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=[])

    async def boom(conn, agency_id):
        raise RuntimeError("simulated recent-daily fetch failure")

    monkeypatch.setattr(reports_mod, "_fetch_recent_daily_rows", boom)

    # __wrapped__: the raw endpoint function under slowapi's @limiter.limit,
    # which otherwise requires a real starlette Request to check the rate
    # limit — irrelevant to the logging behavior under test here.
    endpoint = reports_mod.forecast_overview.__wrapped__
    with caplog.at_level(logging.WARNING, logger="api.routers.reports"):
        result = await endpoint(request=None, agency_id=1, conn=conn, locale="ja")

    assert result["routes"] == []
    records = [r for r in caplog.records if r.name == "api.routers.reports"]
    assert records, "expected a warning log from forecast_overview on recent-daily fetch failure"
    assert any(r.exc_info for r in records), "expected exc_info=True so the traceback is captured"
