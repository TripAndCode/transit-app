"""analyze's retention floor, without a database: no scope a run builds, and
no date the ledger reports, reaches a date before the floor."""

from datetime import date, timedelta

from pipeline import agg_registry, analyze, ch_aggregates, feed_quality
from pipeline.agg_registry import Kind

FLOOR = date(2025, 2, 2)
BEFORE = FLOOR - timedelta(days=1)


def test_every_dated_scope_loses_its_dates_before_the_floor():
    """Whatever put the day before the floor in a scope, the run must not
    purge or rebuild it."""
    dated = [name for name, table in agg_registry.TABLES.items() if table.kind is Kind.DATED]
    scopes = {name: [BEFORE, FLOOR] for name in dated}
    ch_scopes = {name: [BEFORE, FLOOR] for name in ch_aggregates.MONTHLY_TABLES}
    floored, ch_floored = analyze._above_floor(1, None, None, FLOOR, scopes, ch_scopes)
    assert {name: scope for name, scope in floored.items() if name != feed_quality.TABLE} == {
        name: [FLOOR] for name in dated if name != feed_quality.TABLE
    }
    assert ch_floored == {name: [FLOOR] for name in ch_aggregates.MONTHLY_TABLES}
    assert set(ch_aggregates.INTERNAL_TABLES) <= set(ch_floored)


def test_the_whole_history_and_scorecard_scopes_pass_through():
    scopes = {"agg_route_stats": None, feed_quality.TABLE: [BEFORE]}
    floored, _ = analyze._above_floor(1, None, None, FLOOR, scopes, {})
    assert floored == scopes


class _Cursor:
    def __init__(self, rows):
        self.rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        pass

    def fetchall(self):
        return self.rows


class _Conn:
    def __init__(self, rows):
        self.rows = rows

    def cursor(self):
        return _Cursor(self.rows)


def test_a_floor_date_that_shrank_reports_no_date_before_the_floor(monkeypatch):
    """A date that lost rows lends to both neighbours, and the day before the
    floor is final."""
    stored = [(date(2025, 1, 31), 5, 5, 0), (BEFORE, 5, 5, 0), (FLOOR, 5, 5, 0), (date(2025, 2, 3), 5, 5, 0)]
    monkeypatch.setattr(
        analyze, "_ledger_counts", lambda agency_id, ch, floor=None: {FLOOR: (3, 3, 0), date(2025, 2, 3): (5, 5, 0)}
    )
    assert analyze._dates_needing_rebuild(1, _Conn(stored), None, FLOOR) == [FLOOR, date(2025, 2, 3)]


def test_a_date_whose_live_rows_left_rebuilds_with_its_neighbours_down_to_the_floor(monkeypatch):
    """The archive replaced the live rows one for one: both counts held."""
    later = FLOOR + timedelta(days=5)
    stored = [(BEFORE, 5, 5, 0), (FLOOR, 5, 5, 3), (later, 5, 5, 3)]
    monkeypatch.setattr(
        analyze, "_ledger_counts", lambda agency_id, ch, floor=None: {FLOOR: (5, 5, 0), later: (5, 5, 0)}
    )
    assert analyze._dates_needing_rebuild(1, _Conn(stored), None, FLOOR) == [
        FLOOR,
        FLOOR + timedelta(days=1),
        later - timedelta(days=1),
        later,
        later + timedelta(days=1),
    ]


def test_a_date_built_before_live_rows_were_counted_is_taken_to_match(monkeypatch):
    stored = [(FLOOR, 5, 5, None)]
    monkeypatch.setattr(analyze, "_ledger_counts", lambda agency_id, ch, floor=None: {FLOOR: (5, 5, 0)})
    assert analyze._dates_needing_rebuild(1, _Conn(stored), None, FLOOR) == []
