"""`_top_delayed_routes`'s counts state the thresholds they counted against,
and the list and its counts leave out the codes a route list leaves out.

A fake connection answers the two reads, so no database is involved.
"""

from datetime import date

from api.range import RangeCtx
from pipeline.reports.overview import DELAYED_ROUTE_MIN, ROUTE_BAND_MIN, _top_delayed_routes
from pipeline.route_catalog import RETIRED, ZERO_DELAY


class _Conn:
    def __init__(self, rows):
        self.rows = rows

    async def fetch(self, sql, *args):
        if "static_routes" in sql:
            return []
        return self.rows


_CTX = RangeCtx(date(2026, 9, 23), date(2026, 9, 29))


async def test_the_count_comes_with_the_threshold_it_used():
    rows = [
        {"route_code": "1", "avg_min": DELAYED_ROUTE_MIN + 1},
        {"route_code": "2", "avg_min": DELAYED_ROUTE_MIN},
        {"route_code": "3", "avg_min": DELAYED_ROUTE_MIN - 0.1},
    ]
    out = await _top_delayed_routes(1, _CTX, _Conn(rows), left_out={})
    assert out["delayed_count"] == 2
    assert out["delayed_threshold_min"] == DELAYED_ROUTE_MIN


def _bands(severe: int, moderate: int, mild: int) -> dict:
    return {
        "severe": {"count": severe, "threshold_min": 5.0},
        "moderate": {"count": moderate, "threshold_min": 3.0},
        "mild": {"count": mild, "threshold_min": 1.5},
    }


async def test_no_routes_still_states_the_thresholds():
    out = await _top_delayed_routes(1, _CTX, _Conn([]), left_out={})
    assert out == {
        "routes": [],
        "delayed_count": 0,
        "delayed_threshold_min": DELAYED_ROUTE_MIN,
        "band_counts": _bands(0, 0, 0),
    }


def test_the_band_bounds_are_the_frontends():
    # The routes-to-check list bands its listed routes on the client with
    # BAND_BOUNDS (frontend/src/components/routesToCheckBands.ts), and its
    # headers, and the Pulse hero, print these counts beside those bands.
    # routesToCheckBands.test.ts pins the same bounds, so moving either side
    # alone fails a test.
    assert ROUTE_BAND_MIN == {"severe": 5.0, "moderate": 3.0, "mild": 1.5}
    assert list(ROUTE_BAND_MIN) == ["severe", "moderate", "mild"]  # worst first


async def test_the_band_counts_cover_every_route_not_only_the_listed_ones():
    # 9.0 .. 0.5 in half-minute steps: the list stops at five, the counts don't.
    rows = [{"route_code": str(i), "avg_min": 9.0 - i * 0.5} for i in range(18)]
    out = await _top_delayed_routes(1, _CTX, _Conn(rows), left_out={})
    assert len(out["routes"]) == 5
    # severe 9.0..5.0, moderate 4.5..3.0, mild 2.5..1.5; 1.0 and 0.5 fall in no band.
    assert out["band_counts"] == _bands(9, 4, 3)


async def test_the_band_counts_band_the_rounded_average_the_list_carries():
    # The client bands each listed route on its rounded avg_min, so a route
    # listed at 5.0 is severe, one listed at 4.99 is moderate, and the same
    # holds at each lower bound.
    rows = [
        {"route_code": "1", "avg_min": 4.996},
        {"route_code": "2", "avg_min": 4.994},
        {"route_code": "3", "avg_min": 2.996},
        {"route_code": "4", "avg_min": 1.496},
        {"route_code": "5", "avg_min": 1.494},
    ]
    out = await _top_delayed_routes(1, _CTX, _Conn(rows), left_out={})
    assert [r["avg_min"] for r in out["routes"]] == [5.0, 4.99, 3.0, 1.5, 1.49]
    assert out["band_counts"] == _bands(1, 2, 1)


_LEFT_OUT = {RETIRED: frozenset({"OLD"}), ZERO_DELAY: frozenset({"ECHO"})}


async def test_a_left_out_route_is_neither_listed_nor_counted():
    """A retired code or a route whose feed reports no delay is not a route to
    check, and the counts beside the list are of the routes it can show."""
    rows = [
        {"route_code": "OLD", "avg_min": 9.0},
        {"route_code": "1", "avg_min": 6.0},
        {"route_code": "2", "avg_min": 2.0},
        {"route_code": "ECHO", "avg_min": 0.0},
        {"route_code": "3", "avg_min": -0.5},
    ]
    out = await _top_delayed_routes(1, _CTX, _Conn(rows), left_out=_LEFT_OUT)
    assert [r["route_code"] for r in out["routes"]] == ["1", "2", "3"]
    assert out["delayed_count"] == 2
    assert out["band_counts"] == _bands(1, 0, 1)


async def test_left_out_routes_ranked_among_them_leave_the_list_full():
    rows = [{"route_code": "OLD", "avg_min": 9.0}] + [{"route_code": str(i), "avg_min": 8.0 - i} for i in range(7)]
    out = await _top_delayed_routes(1, _CTX, _Conn(rows), left_out=_LEFT_OUT)
    assert [r["route_code"] for r in out["routes"]] == ["0", "1", "2", "3", "4"]
