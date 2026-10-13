"""`_top_delayed_routes`'s counts state the thresholds they counted against.

A fake connection answers the two reads, so no database is involved.
"""

from datetime import date

from api.range import RangeCtx
from pipeline.reports.overview import DELAYED_ROUTE_MIN, SEVERE_ROUTE_MIN, _top_delayed_routes


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
    out = await _top_delayed_routes(1, _CTX, _Conn(rows))
    assert out["delayed_count"] == 2
    assert out["delayed_threshold_min"] == DELAYED_ROUTE_MIN


async def test_no_routes_still_states_the_thresholds():
    out = await _top_delayed_routes(1, _CTX, _Conn([]))
    assert out == {
        "routes": [],
        "delayed_count": 0,
        "delayed_threshold_min": DELAYED_ROUTE_MIN,
        "severe_count": 0,
        "severe_threshold_min": SEVERE_ROUTE_MIN,
    }


def test_the_severe_threshold_is_the_frontends_top_band_bound():
    # The routes-to-check list bands its routes on the client with
    # DELAY_THRESHOLDS.severe (BAND_BOUNDS.severe.min), and the Pulse hero and
    # that list's header print this count beside those bands.
    # frontend/src/components/routesToCheckBands.test.ts pins the same 5, so
    # moving either side alone fails a test.
    assert SEVERE_ROUTE_MIN == 5.0


async def test_the_severe_count_covers_every_route_not_only_the_listed_ones():
    rows = [{"route_code": str(i), "avg_min": 9.0 - i * 0.5} for i in range(10)]  # 9.0 .. 4.5
    out = await _top_delayed_routes(1, _CTX, _Conn(rows))
    assert len(out["routes"]) == 5
    assert out["severe_count"] == 9
    assert out["severe_threshold_min"] == SEVERE_ROUTE_MIN


async def test_the_severe_count_bands_the_rounded_average_the_list_carries():
    # The client bands each listed route on its rounded avg_min, so a route
    # listed at 5.0 must be counted, and one listed at 4.99 must not.
    rows = [{"route_code": "1", "avg_min": 4.996}, {"route_code": "2", "avg_min": 4.994}]
    out = await _top_delayed_routes(1, _CTX, _Conn(rows))
    assert [r["avg_min"] for r in out["routes"]] == [5.0, 4.99]
    assert out["severe_count"] == 1
