"""How a route list leaves codes out by reason and keeps them on request."""

import itertools

import pytest

from pipeline.histogram import bucketize
from pipeline.route_catalog import RETIRED, ZERO_DELAY, listed_count, route_list

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
