"""The pure parts of scripts/ch_agg_parity.py."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from scripts.ch_agg_parity import comparable_dates, differences


def test_only_dates_whose_ledger_still_matches_clickhouse_are_compared():
    stored = {date(2026, 9, 1): (10, 12), date(2026, 9, 2): (5, 5), date(2026, 9, 3): (1, 1)}
    live = {date(2026, 9, 1): (10, 12), date(2026, 9, 2): (6, 6)}
    assert comparable_dates(stored, live) == {date(2026, 9, 1)}


def test_differences_ignore_float32_widening_and_the_timezone_an_instant_is_written_in():
    day = date(2026, 9, 1)
    ch = [(1, day, "R1", 0.029999999329447746, datetime(2026, 9, 1, 0, 0))]
    pg = [(1, day, "R1", 0.03, datetime(2026, 9, 1, 9, 0, tzinfo=timezone(timedelta(hours=9))))]
    assert differences(ch, pg, {day}) == ([], [])


def test_differences_report_each_side_and_skip_dates_not_compared():
    day = date(2026, 9, 1)
    ch = [(1, day, Decimal("0.03")), (1, date(2026, 9, 2), Decimal("9.99"))]
    pg = [(1, day, Decimal("0.02"))]
    assert differences(ch, pg, {day}) == ([(1, day, Decimal("0.03"))], [(1, day, Decimal("0.02"))])
