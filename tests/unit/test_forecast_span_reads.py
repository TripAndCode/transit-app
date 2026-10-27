"""The forecast endpoints' date spans only label figures, so a failed span read
degrades to null dates, logged, rather than 500ing a response whose figures
are fine. A route with no pooled rows has nothing for a span to label, so its
span is not read at all."""

import logging
from unittest.mock import AsyncMock

import pytest

import api.routers.reports as reports_mod
from pipeline import flags
from pipeline.reports.forecast import BANDS


@pytest.fixture(autouse=True)
def _no_ml_overlay(monkeypatch):
    monkeypatch.setenv("ML_FORECAST_ENABLED", "false")
    flags.reset_cache()
    yield
    flags.reset_cache()


def _failing_span_conn(rows):
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=rows)
    conn.fetchrow = AsyncMock(side_effect=RuntimeError("simulated span read failure"))
    return conn


def _logged_with_traceback(caplog) -> bool:
    return any(r.name == "api.routers.reports" and r.exc_info for r in caplog.records)


async def test_overview_degrades_to_null_spans_when_the_span_read_fails(caplog):
    endpoint = reports_mod.forecast_overview.__wrapped__
    with caplog.at_level(logging.WARNING, logger="api.routers.reports"):
        result = await endpoint(request=None, agency_id=1, conn=_failing_span_conn([]), locale="ja")

    assert result["history_from"] is None
    assert result["history_to"] is None
    assert len(result["grid"]) == 7 * len(BANDS)
    assert _logged_with_traceback(caplog)


async def test_heatmap_degrades_to_null_spans_when_the_span_read_fails(caplog):
    rows = [{"dow": 1, "hour": 8, "avg_min": 5.0, "samples": 100}]
    endpoint = reports_mod.forecast_heatmap.__wrapped__
    with caplog.at_level(logging.WARNING, logger="api.routers.reports"):
        result = await endpoint(request=None, route="R1", agency_id=1, conn=_failing_span_conn(rows), locale="ja")

    assert result["history_from"] is None
    assert result["history_to"] is None
    assert next(c for c in result["cells"] if c["dow"] == 1 and c["hour"] == 8)["expected_avg_min"] == 5.0
    assert _logged_with_traceback(caplog)


async def test_heatmap_skips_the_span_read_for_a_route_with_no_pooled_rows():
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=[])
    conn.fetchrow = AsyncMock()

    result = await reports_mod.forecast_heatmap.__wrapped__(
        request=None, route="NO-SUCH-ROUTE", agency_id=1, conn=conn, locale="ja"
    )

    conn.fetchrow.assert_not_awaited()
    assert result["history_from"] is None
    assert result["history_to"] is None
