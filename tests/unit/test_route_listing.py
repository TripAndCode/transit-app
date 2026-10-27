"""How a route list leaves codes out by reason and keeps them on request."""

import itertools
from datetime import date, timedelta

import pytest

from api.range import MAX_RANGE_DAYS, RangeCtx
from pipeline import route_catalog
from pipeline.histogram import bucketize
from pipeline.route_catalog import RETIRED, ZERO_DELAY, listed_count, route_list, rows_to_fetch

# Ranked order as a report returns it; only the first element, the route code, matters.
ROWS = [("C1", 1), ("R1", 2), ("Z1", 3), ("C2", 4), ("Z2", 5), ("C3", 6)]
LEFT_OUT = {RETIRED: frozenset({"R1"}), ZERO_DELAY: frozenset({"Z1", "Z2"})}
MODES = [frozenset(kept) for size in range(3) for kept in itertools.combinations((RETIRED, ZERO_DELAY), size)]


def _codes(rows):
    return [r[0] for r in rows]


@pytest.mark.parametrize(
    ("kept", "expected"),
    [
        (frozenset(), ["C1", "C2"]),
        (frozenset({RETIRED}), ["C1", "R1", "C2"]),
        (frozenset({ZERO_DELAY}), ["C1", "Z1", "C2"]),
        (frozenset({RETIRED, ZERO_DELAY}), ["C1", "R1", "Z1", "C2"]),
    ],
)
def test_each_kept_reason_adds_only_its_own_rows(kept, expected):
    rows, counts = route_list(ROWS, 2, LEFT_OUT, kept)
    assert _codes(rows) == expected
    assert counts == {RETIRED: 1, ZERO_DELAY: 1}


def test_every_mode_lists_the_same_routes_outside_the_left_out_sets():
    for n in range(1, len(ROWS) + 2):
        listed = [_codes(route_list(ROWS, n, LEFT_OUT, kept)[0]) for kept in MODES]
        current = [[c for c in codes if c.startswith("C")] for codes in listed]
        assert all(c == current[0] for c in current), n
        counts = [route_list(ROWS, n, LEFT_OUT, kept)[1] for kept in MODES]
        assert all(c == counts[0] for c in counts), n


def test_a_list_shorter_than_n_counts_every_left_out_row():
    rows, counts = route_list(ROWS, 10, LEFT_OUT, frozenset())
    assert _codes(rows) == ["C1", "C2", "C3"]
    assert counts == {RETIRED: 1, ZERO_DELAY: 2}


def test_left_out_rows_past_the_nth_listed_route_are_not_counted():
    rows, counts = route_list(ROWS, 1, LEFT_OUT, frozenset({RETIRED, ZERO_DELAY}))
    assert _codes(rows) == ["C1"]
    assert counts == {RETIRED: 0, ZERO_DELAY: 0}


def test_nothing_left_out_lists_the_first_n_rows():
    rows, counts = route_list(ROWS, 3, {}, frozenset())
    assert _codes(rows) == ["C1", "R1", "Z1"]
    assert counts == {}


@pytest.mark.parametrize(
    ("kept", "expected"),
    [
        (frozenset(), 3),
        (frozenset({RETIRED}), 4),
        (frozenset({ZERO_DELAY}), 5),
        (frozenset({RETIRED, ZERO_DELAY}), 6),
    ],
)
def test_the_listed_count_takes_the_kept_reasons_rows(kept, expected):
    assert listed_count(ROWS, LEFT_OUT, kept) == expected


def test_zero_opens_its_histogram_bucket():
    """zero_delay_route_codes reads a route as all zeros from its bucket and its
    sum, which holds only while no early departure shares 0's bucket."""
    assert bucketize(-1) != bucketize(0)


@pytest.mark.parametrize("n", range(1, len(ROWS) + 1))
def test_a_fetch_of_rows_to_fetch_lists_n_routes_whatever_it_leaves_out(n):
    """A list cut to ``rows_to_fetch`` ranked rows still lists the ``n`` routes
    the whole ranking would, however many left-out codes rank among them."""
    fetched = ROWS[: rows_to_fetch(n, LEFT_OUT)]
    assert route_list(fetched, n, LEFT_OUT, frozenset())[0] == route_list(ROWS, n, LEFT_OUT, frozenset())[0]


def test_nothing_left_out_fetches_n_rows():
    assert rows_to_fetch(5, {}) == 5


async def test_a_list_narrowed_to_named_routes_reads_and_leaves_out_nothing(monkeypatch):
    """A routes filter narrows a list to the routes it names, and a route asked
    for by name is listed as it always was, so none of its rows is left out
    and there is nothing to read."""

    async def unread(*_args):
        raise AssertionError("the left-out set was read for a list narrowed to named routes")

    monkeypatch.setattr(route_catalog, "left_out_route_codes", unread)
    ctx = RangeCtx(from_date=date(2026, 9, 24), to_date=date(2026, 10, 23), routes=("OLD", "C1"))
    assert await route_catalog.left_out_unless_named(None, 7, ctx) == {}


async def test_a_list_over_every_route_leaves_out_the_whole_set(monkeypatch):
    async def left_out(conn, agency_id, ctx):
        return LEFT_OUT

    monkeypatch.setattr(route_catalog, "left_out_route_codes", left_out)
    ctx = RangeCtx(from_date=date(2026, 9, 24), to_date=date(2026, 10, 23))
    assert await route_catalog.left_out_unless_named(None, 7, ctx) == LEFT_OUT


class _SpanConn:
    """Answers the agency's analysed span, the one read the pooled judgement makes itself."""

    def __init__(self, first, last):
        self.span = {"first": first, "last": last}

    async def fetchrow(self, sql, *args):
        return self.span


LAST_DAY = date(2026, 10, 23)


@pytest.mark.parametrize(
    ("first", "zero_delay_from"),
    [
        (date(2026, 7, 12), date(2026, 7, 12)),
        (date(2023, 1, 1), LAST_DAY - timedelta(days=MAX_RANGE_DAYS - 1)),
    ],
    ids=["whole span", "clipped to the widest window"],
)
async def test_a_pooled_list_judges_retirement_on_every_code_and_zeros_over_a_bounded_window(
    monkeypatch, first, zero_delay_from
):
    """An aggregate that pools every analysed day has no date to window, so
    each of its codes is judged retired on the agency's last days alone,
    however long ago it was last seen. The zero-delay judgement reads every
    day it spans, so it reaches back at most the widest window a route list
    may be asked about."""
    asked: dict[str, object] = {}

    async def retired_among(conn, agency_id, codes, last_day):
        asked["retired"] = (codes, last_day)
        return frozenset({"OLD", "BOTH"})

    async def zero_delay(conn, agency_id, ctx):
        asked["zero_delay"] = (ctx.from_date, ctx.to_date)
        return frozenset({"ECHO", "BOTH"})

    monkeypatch.setattr(route_catalog, "retired_among", retired_among)
    monkeypatch.setattr(route_catalog, "zero_delay_route_codes", zero_delay)
    codes = ["C1", "OLD", "ECHO", "BOTH"]
    got = await route_catalog.pooled_left_out_codes(_SpanConn(first, LAST_DAY), 7, codes)
    assert got == {RETIRED: frozenset({"OLD", "BOTH"}), ZERO_DELAY: frozenset({"ECHO"})}
    assert asked == {"retired": (frozenset(codes), LAST_DAY), "zero_delay": (zero_delay_from, LAST_DAY)}


async def test_a_pooled_list_of_an_agency_without_analysed_days_leaves_out_nothing():
    assert await route_catalog.pooled_left_out_codes(_SpanConn(None, None), 7, ["C1"]) == {}
