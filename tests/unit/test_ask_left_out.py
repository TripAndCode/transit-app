"""Ask's route rankings leave out what every route list leaves out.

A retired code or a route whose feed reports no delay at all
(:mod:`pipeline.route_catalog`) is not ranked and not counted in the answer,
unless the scope's routes filter names it. The rankings and the left-out set
are faked, so no database is involved.
"""

from datetime import date
from decimal import Decimal

import pytest

from api.range import RangeCtx
from pipeline import route_catalog
from pipeline.query import tools
from pipeline.route_catalog import RETIRED, ZERO_DELAY

_CTX = RangeCtx(from_date=date(2026, 9, 24), to_date=date(2026, 10, 23))
_LEFT_OUT = {RETIRED: frozenset({"OLD"}), ZERO_DELAY: frozenset({"ECHO"})}


class _NoCalendarConn:
    async def fetch(self, *_args):
        return []


def _ranking(code):
    return (code, None, Decimal("1.00"), Decimal("1.00"), Decimal("2.00"), 300)


def _on_time(code):
    return (code, None, Decimal("90.0"), Decimal("1.00"), 300)


def _worst(code):
    return (code, None, 12, Decimal("1.00"), 300)


def _compared(code):
    return (code, Decimal("1.00"), Decimal("2.00"), Decimal("1.00"), Decimal("1.00"))


# Best first: the zero-delay route leads, a retired code sits among the rest.
_ORDER = ["ECHO", "C1", "OLD", "C2", "C3"]


@pytest.fixture
def fetched(monkeypatch):
    """The limit each ranking was fetched with, and the left-out set faked."""
    limits: list[int | None] = []

    def ranked(shape):
        async def fake(*_args, limit=None, **_kwargs):
            limits.append(limit)
            return [shape(code) for code in _ORDER][:limit]

        return fake

    async def left_out(conn, agency_id, ctx):
        return _LEFT_OUT

    monkeypatch.setattr(route_catalog, "left_out_route_codes", left_out)
    monkeypatch.setattr(tools, "compute_ranking", ranked(_ranking))
    monkeypatch.setattr(tools, "compute_on_time", ranked(_on_time))
    monkeypatch.setattr(tools, "compute_worst_5min", ranked(_worst))
    monkeypatch.setattr(tools, "compute_compare_ranking", ranked(_compared))
    return limits


@pytest.mark.parametrize(
    ("tool", "args", "summary"),
    [
        ("top_n", {"metric": "avg_delay", "best_first": True, "n": 2}, "On-time ranking, top 2 routes"),
        ("top_n", {"metric": "on_time_rate", "n": 2}, "On-time rate ranking, top 2 routes"),
        ("top_n", {"metric": "worst_5min", "n": 2}, "Count of delays over 5 min ranking, top 2 routes"),
        ("on_time_rate", {"n": 2}, "On-time rate (within 1 min) — top 2 routes"),
    ],
)
async def test_a_ranking_lists_n_routes_past_the_left_out_codes(fetched, tool, args, summary):
    result = await tools.dispatch(tool, args, _CTX, _NoCalendarConn(), 7, locale="en")
    assert [r[0] for r in result.rows] == ["C1", "C2"]
    # Enough rows to list n routes past every left-out code.
    assert fetched == [4]
    assert result.summary == summary


async def test_the_answer_names_no_left_out_route_in_either_language(fetched):
    for locale, summary in (("ja", "定時運行ランキング 上位2路線"), ("en", "On-time ranking, top 2 routes")):
        result = await tools.dispatch(
            "top_n", {"metric": "avg_delay", "best_first": True, "n": 2}, _CTX, _NoCalendarConn(), 7, locale=locale
        )
        assert result.summary == summary
        text = tools.render_tool_result(result, locale=locale)
        assert "ECHO" not in text and "OLD" not in text


async def test_a_ranking_of_only_left_out_routes_has_no_data(fetched, monkeypatch):
    async def only_left_out(*_args, **_kwargs):
        return [_ranking("ECHO"), _ranking("OLD")]

    monkeypatch.setattr(tools, "compute_ranking", only_left_out)
    result = await tools.dispatch("top_n", {"metric": "avg_delay"}, _CTX, _NoCalendarConn(), 7, locale="en")
    assert result.kind == "empty"
    assert result.summary == "No data available."


async def test_a_route_the_scope_names_is_ranked_as_before(fetched):
    named = RangeCtx(from_date=_CTX.from_date, to_date=_CTX.to_date, routes=("ECHO", "C1", "C2"))
    result = await tools.dispatch(
        "top_n", {"metric": "avg_delay", "best_first": True, "n": 2}, named, _NoCalendarConn(), 7, locale="en"
    )
    assert [r[0] for r in result.rows] == ["ECHO", "C1"]


async def test_the_weekday_weekend_comparison_leaves_them_out(fetched):
    result = await tools._tool_compare_segments({"dimension": "dow"}, _CTX, _NoCalendarConn(), 7, "en")
    assert [r[0] for r in result.rows] == ["C1", "C2", "C3"]
    assert fetched == [52]


async def test_the_comparison_of_a_named_route_answers_about_it(fetched):
    result = await tools._tool_compare_segments({"dimension": "dow", "route": "ECHO"}, _CTX, _NoCalendarConn(), 7, "en")
    assert result.rows[0][0] == "ECHO"
