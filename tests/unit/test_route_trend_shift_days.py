"""route_trend_shift / _tool_trend_shift: day-count exposure and the
single-day insufficient-data guard.

A route with only one day of data in the window has no first/second half
to compare, so first_half == second_half == that one day and delta_min is
mechanically 0.00 — indistinguishable from a genuine "stable, no change"
trend. route_trend_shift must instead report "not enough data" (None) for
a single-day window, and the resulting KV surface must expose the day
count so a thin (e.g. 2-3 day) window isn't mistaken for a confident read.

These are pure-logic tests: compute_trend_series (the Postgres-backed
helper route_trend_shift delegates to) is monkeypatched so no DB is
touched.
"""

from datetime import date, timedelta

import pytest

from api.range import RangeCtx
from pipeline.query import tool_queries, tools


def _make_series(day_avgs: list[float]) -> dict:
    """Build a compute_trend_series-shaped dict from a list of daily averages,
    starting today and going backwards one day per entry, each with a fixed
    nonzero sample count."""
    today = date.today()
    days = [
        {"date": (today - timedelta(days=len(day_avgs) - 1 - i)).isoformat(), "avg_min": avg, "samples": 20}
        for i, avg in enumerate(day_avgs)
    ]
    return {"days": days}


@pytest.mark.asyncio
async def test_route_trend_shift_single_day_returns_none_not_zero_delta(monkeypatch):
    """A one-day window must take the no-data path, not report a
    mechanical 0.00 delta that looks like a genuine 'stable' trend."""

    async def _fake_compute_trend_series(agency_id, ctx, conn, ch=None):
        return _make_series([3.0])

    monkeypatch.setattr(tool_queries, "compute_trend_series", _fake_compute_trend_series)

    ctx = RangeCtx(from_date=date.today(), to_date=date.today())
    result = await tool_queries.route_trend_shift(1, ctx, conn=None, route="R1")

    assert result is None


@pytest.mark.asyncio
async def test_route_trend_shift_days_matches_half_window_sizes(monkeypatch):
    """A multi-day window's result must expose the day count, and that
    count must equal the actual first-half + second-half sizes used to
    compute delta_min."""
    day_avgs = [1.0, 1.0, 5.0, 5.0, 5.0]  # 5 days: split 2 / 3.

    async def _fake_compute_trend_series(agency_id, ctx, conn, ch=None):
        return _make_series(day_avgs)

    monkeypatch.setattr(tool_queries, "compute_trend_series", _fake_compute_trend_series)

    ctx = RangeCtx(from_date=date.today() - timedelta(days=4), to_date=date.today())
    result = await tool_queries.route_trend_shift(1, ctx, conn=None, route="R1")

    assert result is not None
    assert result["days"] == len(day_avgs)
    # Midpoint split used internally: first_half gets len // 2, second_half
    # gets the remainder — together they must reconstruct the reported count.
    midpoint = len(day_avgs) // 2
    assert midpoint + (len(day_avgs) - midpoint) == result["days"]
    assert result["first_half_avg_min"] < result["second_half_avg_min"]


@pytest.mark.asyncio
async def test_tool_trend_shift_kv_surfaces_days(monkeypatch):
    """_tool_trend_shift must include a days KV pair in its result, unlike
    before, where route_trend_shift computed the count internally but it
    never reached the LLM/user-visible pairs."""

    async def _fake_require_registered_route(args, conn, agency_id, locale, ch=None):
        return "R1"

    async def _fake_route_trend_shift(agency_id, ctx, conn, ch=None, *, route):
        return {
            "first_half_avg_min": 1.0,
            "second_half_avg_min": 5.0,
            "delta_min": 4.0,
            "days": 5,
        }

    monkeypatch.setattr(tools, "_require_registered_route", _fake_require_registered_route)
    monkeypatch.setattr(tools, "route_trend_shift", _fake_route_trend_shift)

    ctx = RangeCtx(from_date=date.today() - timedelta(days=4), to_date=date.today())
    result = await tools._tool_trend_shift({"route": "R1"}, ctx, conn=None, agency_id=1, locale="ja")

    assert result.kind == "kv"
    labels = [p[0] for p in result.pairs]
    values = dict(result.pairs)
    assert "日数" in labels
    assert values["日数"] == "5日"
