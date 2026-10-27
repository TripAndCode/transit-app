"""`_top_delayed_routes`'s counts state the thresholds they counted against,
and the list and its counts leave out the codes a route list leaves out.

A fake connection answers the reads, so no database is involved.
"""

from datetime import date

from api.range import RangeCtx
from pipeline.reports.overview import (
    DELAYED_ROUTE_MIN,
    MOVER_MIN_SAMPLES,
    ROUTE_BAND_MIN,
    TOP_DELAYED_MIN_SAMPLES,
    _top_delayed_routes,
)
from pipeline.reports.rankings import RANKING_MIN_SAMPLES
from pipeline.route_catalog import RETIRED, ZERO_DELAY

_CTX = RangeCtx(date(2026, 9, 23), date(2026, 9, 29))
_BASE = RangeCtx(date(2026, 9, 16), date(2026, 9, 22))


def _row(code: str, avg: float, samples: int = TOP_DELAYED_MIN_SAMPLES) -> dict:
    return {"route_code": code, "avg_min": avg, "samples": samples}


class _Conn:
    """Answers each window's per-route read by the window's first day, the
    query's first date parameter."""

    def __init__(self, rows, base_rows=()):
        self.by_window = {
            _CTX.from_date.isoformat(): [_with_samples(r) for r in rows],
            _BASE.from_date.isoformat(): list(base_rows),
        }

    async def fetch(self, sql, *args):
        if "static_routes" in sql:
            return []
        return self.by_window[str(args[1])]


def _with_samples(row: dict) -> dict:
    return {"samples": TOP_DELAYED_MIN_SAMPLES, **row}


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
        "route_count": 0,
        "min_samples": TOP_DELAYED_MIN_SAMPLES,
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


def test_the_floor_is_the_routes_rankings():
    # The list's link opens the Routes ranking over the same days, which
    # keeps routes observed RANKING_MIN_SAMPLES times or more by default.
    assert TOP_DELAYED_MIN_SAMPLES == RANKING_MIN_SAMPLES


async def test_a_thinly_observed_route_is_neither_listed_nor_counted():
    """A route observed fewer times than the floor would top the list on a
    handful of trips, so it is left out of the list, every count and the
    number of routes the counts are out of."""
    rows = [
        _row("THIN", 12.0, TOP_DELAYED_MIN_SAMPLES - 1),
        _row("1", 6.0),
        _row("2", 2.0),
        _row("3", 0.5),
    ]
    out = await _top_delayed_routes(1, _CTX, _Conn(rows), left_out={})
    assert [r["route_code"] for r in out["routes"]] == ["1", "2", "3"]
    assert out["band_counts"] == _bands(1, 0, 1)
    assert out["delayed_count"] == 2
    assert out["route_count"] == 3
    assert out["min_samples"] == TOP_DELAYED_MIN_SAMPLES


async def test_the_route_count_is_of_every_route_ranked_not_only_the_listed_ones():
    rows = [_row(str(i), 9.0 - i * 0.5) for i in range(18)]
    out = await _top_delayed_routes(1, _CTX, _Conn(rows), left_out=_LEFT_OUT)
    assert len(out["routes"]) == 5
    assert out["route_count"] == 18


async def test_the_route_count_leaves_out_what_the_list_leaves_out():
    rows = [_row("OLD", 9.0), _row("1", 6.0), _row("ECHO", 0.0)]
    out = await _top_delayed_routes(1, _CTX, _Conn(rows), left_out=_LEFT_OUT)
    assert out["route_count"] == 1


async def test_each_listed_route_carries_its_change_from_the_window_before():
    """The change is the movers' statistic: the current average less the
    previous window's, each window's average unrounded."""
    rows = [_row("UP", 6.004), _row("DOWN", 4.0)]
    base = [
        {"route_code": "UP", "avg_min": 4.0, "samples": MOVER_MIN_SAMPLES},
        {"route_code": "DOWN", "avg_min": 5.506, "samples": 500},
    ]
    out = await _top_delayed_routes(1, _CTX, _Conn(rows, base), left_out={}, base_ctx=_BASE)
    assert {r["route_code"]: r["delta_min"] for r in out["routes"]} == {"UP": 2.0, "DOWN": -1.51}


async def test_a_route_thin_or_absent_in_the_window_before_has_no_change():
    rows = [_row("NEW", 6.0), _row("THIN", 5.0)]
    base = [{"route_code": "THIN", "avg_min": 1.0, "samples": MOVER_MIN_SAMPLES - 1}]
    out = await _top_delayed_routes(1, _CTX, _Conn(rows, base), left_out={}, base_ctx=_BASE)
    assert [r["delta_min"] for r in out["routes"]] == [None, None]


async def test_no_window_before_means_no_change():
    out = await _top_delayed_routes(1, _CTX, _Conn([_row("1", 6.0)]), left_out={})
    assert out["routes"][0]["delta_min"] is None
