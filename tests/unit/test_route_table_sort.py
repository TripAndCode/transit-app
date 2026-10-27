"""How the Routes table orders its rows under each sort."""

from decimal import Decimal

import pytest

from pipeline.reports.route_table import ROUTE_TABLE_SORTS, sort_route_table

# (route_code, avg_min, on_time_pct, late5_pct, late5_count, samples)
ROWS = [
    ("A", Decimal("4.00"), Decimal("40.0"), Decimal("60.0"), 15, 25),
    ("B", Decimal("7.33"), Decimal("20.0"), Decimal("40.0"), 10, 25),
    ("C", Decimal("6.67"), Decimal("80.0"), Decimal("20.0"), 5, 25),
    ("D", Decimal("2.92"), Decimal("50.0"), Decimal("50.0"), 25, 50),
]


@pytest.mark.parametrize(
    ("sort", "worst_first"),
    [
        ("avg", ["B", "C", "A", "D"]),
        ("on_time", ["B", "A", "D", "C"]),
        ("late5_share", ["A", "D", "B", "C"]),
        ("late5_count", ["D", "A", "B", "C"]),
    ],
)
def test_each_sort_puts_the_routes_needing_attention_first_and_best_reverses_it(sort, worst_first):
    assert [r[0] for r in sort_route_table(ROWS, sort, "worst")] == worst_first
    assert [r[0] for r in sort_route_table(ROWS, sort, "best")] == worst_first[::-1]


@pytest.mark.parametrize("sort", sorted(ROUTE_TABLE_SORTS))
@pytest.mark.parametrize("order", ["worst", "best"])
def test_a_tie_breaks_on_the_route_code_either_way(sort, order):
    tied = [("T2", *ROWS[0][1:]), ("T1", *ROWS[0][1:]), ("T3", *ROWS[0][1:])]
    assert [r[0] for r in sort_route_table(tied, sort, order)] == ["T1", "T2", "T3"]


def test_sorting_leaves_the_input_alone():
    rows = list(ROWS)
    sort_route_table(rows, "on_time", "worst")
    assert rows == ROWS
