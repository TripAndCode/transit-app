"""The pure parts of scripts/ch_agg_parity.py."""

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from pipeline import ch_aggregates
from scripts import ch_agg_parity
from scripts.ch_agg_parity import (
    _timed,
    comparable,
    comparable_dates,
    differences,
    normalized,
    parity,
    timing,
    whole_history_current,
)


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
        self.queries = []
        self.raw = []

    def query(self, sql, parameters=None):
        self.queries.append(sql)
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


def test_differences_compare_array_columns():
    """A histogram column comes back as a list, which a set of rows cannot hold as is."""
    day = date(2026, 9, 1)
    ch = [(1, day, "R1", [0, 2, 1])]
    pg = [(1, day, "R1", [0, 2, 1])]
    assert differences(ch, pg, {day}) == ([], [])
    only_ch, only_pg = differences(ch, [(1, day, "R1", [0, 1, 2])], {day})
    assert only_ch == [(1, day, "R1", (0, 2, 1))]
    assert only_pg == [(1, day, "R1", (0, 1, 2))]


def test_whole_history_is_compared_only_when_every_date_is_current():
    """A whole-history row spans every date, so one stale or unledgered date makes
    the two stores legitimately differ."""
    first, second = date(2026, 9, 1), date(2026, 9, 2)
    stored = {first: (5, 5), second: (6, 6)}
    assert whole_history_current(stored, {first: (5, 5), second: (6, 6)})
    assert not whole_history_current(stored, {first: (5, 5), second: (7, 7)})
    assert not whole_history_current(stored, {first: (5, 5), second: (6, 6), date(2026, 9, 3): (1, 1)})


def _joins_the_mirror(sql: str) -> bool:
    return ch_aggregates.STATIC_STOP_TIMES in sql and "SELECT fingerprint" not in sql


def test_parity_leaves_the_static_joined_tables_until_the_schedule_is_mirrored(capsys, monkeypatch):
    """They join ClickHouse's schedule mirror, which only an analyze fills. Compared
    before it, every row Postgres holds would read as missing from ClickHouse."""
    day = date(2026, 9, 1)
    # A date ClickHouse has and the ledger lacks keeps the whole-history tables out of it.
    monkeypatch.setattr(ch_agg_parity, "_ledger_counts", lambda agency_id, ch: {day: (5, 5), date(2026, 9, 2): (1, 1)})
    ch = _ClickHouse()
    assert parity(ch, _Conn([(day, 5, 5)]), 1) == 0
    assert [sql for sql in ch.queries if _joins_the_mirror(sql)] == []
    assert len(ch.queries) == len([t for t in ch_aggregates.TABLES if t not in ch_aggregates.STATIC_JOINED_TABLES]) + 1
    assert "no schedule mirror" in capsys.readouterr().out


class _Mirrored(_ClickHouse):
    """Holds a schedule mirror for every agency, and no other rows."""

    def query(self, sql, parameters=None):
        super().query(sql, parameters)
        return _Result([("fp",)] if "SELECT fingerprint" in sql or "system.tables" in sql else [])


def test_parity_compares_the_static_joined_tables_once_the_schedule_is_mirrored(monkeypatch):
    day = date(2026, 9, 1)
    monkeypatch.setattr(ch_agg_parity, "_ledger_counts", lambda agency_id, ch: {day: (5, 5), date(2026, 9, 2): (1, 1)})
    ch = _Mirrored()
    assert parity(ch, _Conn([(day, 5, 5)]), 1) == 0
    joined = [t for t in ch_aggregates.TABLES if t in ch_aggregates.STATIC_JOINED_TABLES]
    assert len([sql for sql in ch.queries if _joins_the_mirror(sql)]) == len(joined)


def test_timing_leaves_the_static_joined_tables_until_the_schedule_is_mirrored(capsys, monkeypatch):
    """An empty mirror joins nothing, so its figure would be a fast query that measured nothing."""
    monkeypatch.setattr(ch_aggregates, "source_months", lambda ch, agency_id: [202609])
    ch = _ClickHouse()
    assert timing(ch, 1) == 0
    assert [sql for sql in ch.raw if _joins_the_mirror(sql)] == []
    assert len(ch.raw) == 1 + len(
        [t for t in ch_aggregates.MONTHLY_TABLES if t not in ch_aggregates.STATIC_JOINED_TABLES]
    )
    assert "no schedule mirror" in capsys.readouterr().out


class _Unmigrated(_ClickHouse):
    """Predates the schema that adds the schedule mirror, so reading it fails."""

    def query(self, sql, parameters=None):
        if f"FROM {ch_aggregates.STATIC_STOP_TIMES}" in sql:
            raise RuntimeError("UNKNOWN_TABLE")
        return super().query(sql, parameters)


def test_both_modes_run_before_the_schema_adds_the_schedule_mirror(monkeypatch):
    day = date(2026, 9, 1)
    monkeypatch.setattr(ch_agg_parity, "_ledger_counts", lambda agency_id, ch: {day: (5, 5), date(2026, 9, 2): (1, 1)})
    monkeypatch.setattr(ch_aggregates, "source_months", lambda ch, agency_id: [202609])
    assert parity(_Unmigrated(), _Conn([(day, 5, 5)]), 1) == 0
    assert timing(_Unmigrated(), 1) == 0


def test_parity_leaves_agg_stop_routes_until_the_schedule_is_mirrored(monkeypatch):
    """Every date is current, so the whole-history tables are compared, all but this one."""
    day = date(2026, 9, 1)
    monkeypatch.setattr(ch_agg_parity, "_ledger_counts", lambda agency_id, ch: {day: (5, 5)})
    ch = _ClickHouse()
    parity(ch, _Conn([(day, 5, 5)]), 1)
    assert [sql for sql in ch.queries if _joins_the_mirror(sql)] == []
    assert len([sql for sql in ch.queries if "GROUP BY" in sql]) > len(ch_aggregates.TABLES)


def test_a_stop_route_list_compares_whatever_its_order():
    """ClickHouse sorts a stop's routes by bytes, the Postgres copy by its collation."""
    assert comparable("agg_stop_routes", (1, "S1", "r1,R1,2507 9")) == comparable(
        "agg_stop_routes", (1, "S1", "2507 9,R1,r1")
    )
    assert comparable("agg_stop_routes", (1, "S1", "R1")) != comparable("agg_stop_routes", (1, "S1", "R1,r1"))


def test_a_postgres_time_compares_equal_to_its_clickhouse_text():
    """agg_route_hour's scheduled_time is a `time` in Postgres and HH:MM:SS text in ClickHouse."""
    assert normalized(time(7, 45)) == normalized("07:45:00")
