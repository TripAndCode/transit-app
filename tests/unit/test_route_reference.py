"""The routes' trailing reference, where its decisions need no database."""

from datetime import date

import pytest

from api.range import RangeCtx
from pipeline.reports.route_table import compute_route_references, route_references_for


class _NoQueries:
    """A connection that fails the test on any use."""

    def __getattr__(self, name):
        raise AssertionError(f"the reference used its connection ({name})")


PERIOD = {"from_date": date(2026, 5, 18), "to_date": date(2026, 5, 24)}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scope", "window"),
    [
        ({"time_band": "morning"}, (60, 300)),
        ({"time_band": "morning", "routes": ("R1",), "service": "平日"}, (None, 60)),
        ({}, (60, 1800)),
        ({}, (600, 300)),
    ],
)
async def test_a_scope_no_aggregate_holds_by_date_reads_nothing(scope, window):
    """A time band, or an on-time bound in the histogram's open end buckets,
    has no aggregate to read by date, so the reference decides
    ``not_aggregated`` before it reads the window at all."""
    early, late = window
    refs = await compute_route_references(
        1, RangeCtx(**PERIOD, **scope), _NoQueries(), early_tolerance_sec=early, late_tolerance_sec=late
    )
    assert (refs["reason"], refs["samples"], refs["routes"]) == ("not_aggregated", 0, {})
    assert (refs["window_from"], refs["window_to"], refs["days"]) == ("2026-02-17", "2026-05-17", 90)

    listed = route_references_for(refs, ["R1", "R2"])
    assert listed["routes"] == {
        code: {"on_time_pct": None, "avg_min": None, "samples": 0, "reason": "not_aggregated"} for code in ("R1", "R2")
    }
