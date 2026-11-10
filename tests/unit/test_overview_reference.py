"""Pulse's trailing reference, where its decisions need no database."""

from datetime import date

import pytest

from api.range import RangeCtx
from pipeline.reports import overview as ov


class _NoQueries:
    """A connection that fails the test on any use."""

    def __getattr__(self, name):
        raise AssertionError(f"the reference used its connection ({name})")


@pytest.mark.asyncio
@pytest.mark.parametrize("service", ["all", "平日"])
async def test_a_time_band_with_routes_reads_nothing(service):
    """No aggregate holds a route's departures by date and hour, so the
    reference decides ``not_aggregated`` before it reads the window at all."""
    period = RangeCtx(
        from_date=date(2026, 5, 18), to_date=date(2026, 5, 24), time_band="morning", routes=("R1",), service=service
    )
    ref = await ov._reference(1, ov._reference_ctx(period), _NoQueries())
    assert (ref["reason"], ref["avg_min"], ref["samples"]) == ("not_aggregated", None, 0)
    assert (ref["window_from"], ref["window_to"], ref["days"]) == ("2026-02-17", "2026-05-17", 90)
