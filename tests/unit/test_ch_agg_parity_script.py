"""The pure parts of scripts/ch_agg_parity.py."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from scripts.ch_agg_parity import _timed, comparable_dates, differences, parity


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


class _Result:
    def __init__(self, rows):
        self.result_rows = rows


class _ClickHouse:
    """Answers every query with no rows, and records what it was asked."""

    def __init__(self):
        self.raw = []

    def query(self, sql, parameters=None):
        return _Result([])

    def raw_query(self, sql, parameters=None):
        self.raw.append(sql)
        return b""


class _Cursor:
    def __init__(self, rows):
        self._rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        pass

    def fetchall(self):
        return self._rows


class _Conn:
    def __init__(self, rows):
        self._rows = rows

    def cursor(self):
        return _Cursor(self._rows)


def test_parity_fails_when_no_ledger_date_is_current(capsys):
    """ClickHouse holds none of the ledger's dates, so nothing can be compared."""
    assert parity(_ClickHouse(), _Conn([(date(2026, 9, 1), 5, 5)]), 1) == 1
    assert "nothing to compare" in capsys.readouterr().out


def test_timing_runs_each_select_in_full_and_discards_its_rows():
    """FORMAT Null makes the server produce every column; a count() around the
    SELECT would let it skip the aggregates nothing reads."""
    ch = _ClickHouse()
    _timed(ch, "SELECT 1", {})
    assert ch.raw == ["SELECT 1 FORMAT Null"]
