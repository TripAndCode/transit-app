"""What a run with nothing to rebuild is allowed to cost.

Drives :func:`pipeline.analyze.analyze` against a recording fake connection and
fake ClickHouse client, so the expensive scans a no-op run must not issue can be
asserted without a database. The aggregate *values* those scans would produce
are covered against real engines in tests/pipeline/test_analyze.py; nothing here
restates them.
"""

from datetime import date

import pytest

from pipeline import analyze as analyze_mod
from pipeline.analyze import analyze

AGENCY_ID = 7
LEDGER_DATE = date(2026, 1, 2)
LEDGER_SAMPLES = 41


class _FakeResult:
    def __init__(self, rows):
        self.result_rows = rows


class _EmptyStream:
    """A `query_row_block_stream` result that yields no blocks."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        return iter(())


class FakeClickHouse:
    def __init__(self, ledger):
        self._ledger = ledger
        self.queries: list[str] = []
        self.streams: list[str] = []

    def query(self, sql, parameters=None):
        self.queries.append(sql)
        # The per-date ledger, not agg_feed_health's own build — the two read
        # the same rows and differ only in what they project.
        if "raw_samples" in sql and "clamp_count" not in sql:
            return _FakeResult(self._ledger)
        return _FakeResult([])

    def query_row_block_stream(self, sql, parameters=None):
        self.streams.append(sql)
        return _EmptyStream()

    @property
    def alltime_scans(self) -> list[str]:
        """The full-history dedup slice: typed, and without captured_at."""
        return [s for s in self.streams if "service_type IS NOT NULL" in s and "last_captured_at" not in s]

    @property
    def stop_routes_scans(self) -> list[str]:
        return [s for s in self.streams if "SELECT DISTINCT route_code" in s]


class FakeCursor:
    def __init__(self, recorder):
        self._recorder = recorder
        self._one = None
        self._all: list = []
        self.rowcount = -1

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self._recorder.executed.append(sql)
        self._one, self._all = self._recorder.respond(sql)

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._all

    def copy_expert(self, sql, stream):  # pragma: no cover - no rows are streamed
        self._recorder.executed.append(sql)


class FakeConn:
    """Answers only the probes `analyze` makes; every builder returns no rows."""

    def __init__(self, fingerprint):
        self.executed: list[str] = []
        self._fingerprint = fingerprint

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        pass

    def rollback(self):
        pass

    def respond(self, sql):
        if "FROM static_stops" in sql:
            return None, []
        if "static_fingerprint FROM agg_meta" in sql:
            return (self._fingerprint,), []
        if "SELECT date, raw_samples FROM agg_feed_health" in sql:
            return None, [(LEDGER_DATE, LEDGER_SAMPLES)]
        if "SUM(clamp_count)" in sql:
            return (0,), []
        if "ingest_strategy FROM agencies" in sql:
            return None, []
        return None, []


def _run(ledger):
    """Run `analyze` for an agency with no static schedule, returning the fakes."""
    fingerprint = analyze_mod._static_fingerprint(AGENCY_ID, None, has_static=False)
    conn = FakeConn(fingerprint)
    ch = FakeClickHouse(ledger)
    analyze(AGENCY_ID, conn, ch)
    return conn, ch


def _noop_run():
    """A run where the ledger already matches ClickHouse: no date needs rebuilding."""
    return _run([(LEDGER_DATE, LEDGER_SAMPLES)])


def _changed_run():
    """A run where one date gained rows since the last build."""
    return _run([(LEDGER_DATE, LEDGER_SAMPLES + 1)])


# ── The all-time slice and the aggregates built from it ──────────────────


def test_a_noop_run_does_not_scan_the_full_history():
    """The all-time slice spans every date, so it is the most expensive read a
    run makes — and a run where no date changed would rebuild the three
    aggregates over it to exactly what already stands."""
    _, ch = _noop_run()

    assert ch.alltime_scans == []


def test_a_run_with_a_changed_date_still_scans_the_full_history():
    """The guard keys off the ledger, not off incrementality in general: an
    all-time aggregate spans the changed date too, so it must be rebuilt whole."""
    _, ch = _changed_run()

    assert len(ch.alltime_scans) == 1


@pytest.mark.parametrize("table", ["agg_route_stats", "agg_route_hour", "agg_route_hour_dow"])
def test_a_noop_run_neither_purges_nor_rebuilds_an_alltime_aggregate(table):
    """Skipping the build without skipping the purge would empty the table."""
    conn, _ = _noop_run()

    assert not [s for s in conn.executed if f"DELETE FROM {table}" in s]
    assert not [s for s in conn.executed if f"INSERT INTO {table}" in s]
    assert not [s for s in conn.executed if f"FROM {table}" in s and s.lstrip().startswith("SELECT")]


@pytest.mark.parametrize("table", ["agg_route_stats", "agg_route_hour", "agg_route_hour_dow"])
def test_a_run_with_a_changed_date_purges_the_alltime_aggregate_whole(table):
    conn, _ = _changed_run()

    assert [s for s in conn.executed if f"DELETE FROM {table} WHERE agency_id" in s]


def test_agg_stop_routes_is_not_skippable_on_a_noop_run():
    """It is the one aggregate the ledger cannot speak for.

    Its key scan reads `updates` with no `dep_delay` filter — deliberately, so
    a stop keeps its route coverage even where every observation lacked a
    usable delay. The ledger counts only rows WITH a `dep_delay`
    (`agg_feed_health.raw_samples`), so an ingest consisting entirely of
    delay-less rows leaves it unmoved while genuinely changing this table.
    Listing this table as skippable would drop those keys silently.
    """
    assert "agg_stop_routes" not in analyze_mod._NOOP_SKIPPABLE_AGG_TABLES


def test_a_noop_run_still_records_this_build_in_agg_meta():
    """The record has to keep pace even when nothing was rebuilt: the
    fingerprint it carries is what the next run compares against."""
    conn, _ = _noop_run()

    assert [s for s in conn.executed if "INSERT INTO agg_meta" in s]


# ── The fingerprint that decides whether a date may be trusted ───────────


def test_the_fingerprint_changes_when_the_plausibility_clamp_changes(monkeypatch):
    """The clamp decides which rows reach every aggregate, so moving it makes
    every already-built date wrong while no row anywhere changed."""
    before = analyze_mod._static_fingerprint(AGENCY_ID, None, has_static=False)
    monkeypatch.setattr(analyze_mod, "MAX_PLAUSIBLE_DELAY_SEC", analyze_mod.MAX_PLAUSIBLE_DELAY_SEC + 1)

    assert analyze_mod._static_fingerprint(AGENCY_ID, None, has_static=False) != before


def test_the_fingerprint_changes_when_the_builder_logic_version_changes(monkeypatch):
    before = analyze_mod._static_fingerprint(AGENCY_ID, None, has_static=False)
    monkeypatch.setattr(analyze_mod, "ANALYZE_LOGIC_VERSION", analyze_mod.ANALYZE_LOGIC_VERSION + 1)

    assert analyze_mod._static_fingerprint(AGENCY_ID, None, has_static=False) != before


def test_the_fingerprint_is_never_empty_without_a_static_schedule():
    """An agency with no schedule still has a clamp and a builder version to
    invalidate against; an empty value would leave it with neither."""
    assert analyze_mod._static_fingerprint(AGENCY_ID, None, has_static=False) != ""
