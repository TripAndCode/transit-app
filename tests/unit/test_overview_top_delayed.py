"""`_top_delayed_routes`'s delayed count states the threshold it counted against.

A fake connection answers the two reads, so no database is involved.
"""

from datetime import date

from api.range import RangeCtx
from pipeline.reports.overview import DELAYED_ROUTE_MIN, _top_delayed_routes


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


async def test_no_routes_still_states_the_threshold():
    out = await _top_delayed_routes(1, _CTX, _Conn([]))
    assert out == {"routes": [], "delayed_count": 0, "delayed_threshold_min": DELAYED_ROUTE_MIN}
