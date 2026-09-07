"""Pure-logic tests for pipeline.reports.ridership's percentage arithmetic.

The weighting itself happens in SQL (see pipeline/reports/ridership.py's
_WEIGHTED_SQL / _WEIGHTED_BY_AGENCY_SQL, exercised against a real DB in
tests/api/test_network.py); this file only covers the pure rounding/
divide-by-zero helper, which needs no DB fixture.
"""

from decimal import Decimal

from pipeline.reports.ridership import _pct


def test_pct_divides_and_rounds_half_up_to_one_decimal():
    # 910/1100*100 = 82.7272... -> 82.7
    assert _pct(Decimal(910), Decimal(1100)) == 82.7


def test_pct_none_when_denominator_is_zero():
    assert _pct(Decimal(0), Decimal(0)) is None


def test_pct_none_when_denominator_is_none():
    assert _pct(None, None) is None


def test_pct_accepts_plain_numeric_types_too():
    assert _pct(90, 100) == 90.0
