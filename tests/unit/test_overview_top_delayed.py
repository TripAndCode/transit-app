"""`_top_delayed_routes`'s counts state the bound they counted against, and
leave out the codes a route list leaves out.

A fake connection answers the reads, so no database is involved.
"""

from datetime import date

from api.range import RangeCtx
from pipeline.reports.overview import SEVERE_ROUTE_MIN, TOP_DELAYED_MIN_SAMPLES, _top_delayed_routes
from pipeline.reports.rankings import RANKING_MIN_SAMPLES
from pipeline.route_catalog import RETIRED, ZERO_DELAY

_CTX = RangeCtx(date(2026, 9, 1), date(2026, 9, 30))


def _row(code: str, avg: float, samples: int = TOP_DELAYED_MIN_SAMPLES) -> dict:
    return {"route_code": code, "avg_min": avg, "samples": samples}


class _Conn:
    """Answers the per-route read with ``rows``."""

    def __init__(self, rows):
        self.rows = [{"samples": TOP_DELAYED_MIN_SAMPLES, **r} for r in rows]

    async def fetch(self, sql, *args):
        return self.rows


def _severe(count: int) -> dict:
    return {"severe": {"count": count, "threshold_min": SEVERE_ROUTE_MIN}}


async def test_no_routes_still_states_the_bound():
    out = await _top_delayed_routes(1, _CTX, _Conn([]), left_out={})
    assert out == {"band_counts": _severe(0), "route_count": 0, "min_samples": TOP_DELAYED_MIN_SAMPLES}


async def test_the_count_covers_every_route_ranked():
    # 9.0 .. 0.5 in half-minute steps: 9.0..5.0 are late.
    rows = [_row(str(i), 9.0 - i * 0.5) for i in range(18)]
    out = await _top_delayed_routes(1, _CTX, _Conn(rows), left_out={})
    assert out["band_counts"] == _severe(9)
    assert out["route_count"] == 18


async def test_the_count_judges_the_rounded_average():
    # A route at 4.996 shows as 5.00 wherever its average is printed, so it
    # counts; one at 4.994 shows as 4.99 and does not.
    out = await _top_delayed_routes(1, _CTX, _Conn([_row("1", 4.996), _row("2", 4.994)]), left_out={})
    assert out["band_counts"] == _severe(1)


_LEFT_OUT = {RETIRED: frozenset({"OLD"}), ZERO_DELAY: frozenset({"ECHO"})}


async def test_a_left_out_route_is_not_counted():
    """A retired code or a route whose feed reports no delay is left out of
    the count and of the routes it is out of, as the Routes table leaves it out."""
    rows = [_row("OLD", 9.0), _row("1", 6.0), _row("2", 2.0), _row("ECHO", 0.0)]
    out = await _top_delayed_routes(1, _CTX, _Conn(rows), left_out=_LEFT_OUT)
    assert out["band_counts"] == _severe(1)
    assert out["route_count"] == 2


def test_the_floor_is_the_routes_tables():
    # The count is out of the routes the routes-to-check preview's table
    # ranks, which keeps routes observed RANKING_MIN_SAMPLES times or more.
    assert TOP_DELAYED_MIN_SAMPLES == RANKING_MIN_SAMPLES


async def test_a_thinly_observed_route_is_not_counted():
    rows = [_row("THIN", 12.0, TOP_DELAYED_MIN_SAMPLES - 1), _row("1", 6.0), _row("2", 0.5)]
    out = await _top_delayed_routes(1, _CTX, _Conn(rows), left_out={})
    assert out == {"band_counts": _severe(1), "route_count": 2, "min_samples": TOP_DELAYED_MIN_SAMPLES}
