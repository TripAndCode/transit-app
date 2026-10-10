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
        self.queries: list[tuple[str, dict]] = []
        self.streams: list[str] = []

    def query(self, sql, parameters=None):
        self.queries.append((sql, parameters or {}))
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

    @property
    def service_delivered_builds(self) -> list[tuple[str, dict]]:
        return [q for q in self.queries if "schedule_relationship_trip" in q[0]]

    @property
    def schedule_revision_builds(self) -> list[tuple[str, dict]]:
        return [q for q in self.queries if "static_version_id AS version" in q[0]]


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

    def __init__(self, *, ledger_rows, ingest_strategy=None, has_static=False):
        self.executed: list[str] = []
        self._fingerprint: str | None = None
        self._ledger_rows = ledger_rows
        self._ingest_strategy = ingest_strategy
        self._has_static = has_static

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        pass

    def rollback(self):
        pass

    def respond(self, sql):
        if "FROM static_stops" in sql:
            return ((1,) if self._has_static else None), []
        if "SELECT count(*), coalesce(sum(" in sql:
            return (0, 0), []
        if "static_fingerprint FROM agg_meta" in sql:
            return (self._fingerprint,), []
        if "SELECT date, raw_samples, total_rows FROM agg_feed_health" in sql:
            return None, self._ledger_rows
        if "SUM(clamp_count)" in sql:
            return (0,), []
        if "ingest_strategy FROM agencies" in sql:
            return ((self._ingest_strategy,) if self._ingest_strategy else None), []
        return None, []


LEDGER_TOTAL = 50  # 41 rows with a dep_delay + 9 without


def _run(ledger, *, stored=None, ingest_strategy=None, has_static=False):
    """Run `analyze`, returning the fakes.

    *ledger* is what ClickHouse answers (date, raw_samples, total_rows);
    *stored* is what agg_feed_health holds, defaulting to the same rows.
    *has_static* gives the agency a (row-less) static schedule, which the
    builders joining it — agg_stop_routes among them — need in order to run.
    """
    conn = FakeConn(
        ledger_rows=stored if stored is not None else ledger,
        ingest_strategy=ingest_strategy,
        has_static=has_static,
    )
    conn._fingerprint = analyze_mod._static_fingerprint(
        AGENCY_ID, conn, has_static=has_static, ingest_strategy=ingest_strategy
    )
    ch = FakeClickHouse(ledger)
    analyze(AGENCY_ID, conn, ch)
    return conn, ch


def _noop_run(**kw):
    """A run where the ledger already matches ClickHouse: no date needs rebuilding."""
    return _run([(LEDGER_DATE, LEDGER_SAMPLES, LEDGER_TOTAL)], **kw)


def _changed_run(**kw):
    """A run where one date gained rows since the last build."""
    return _run(
        [(LEDGER_DATE, LEDGER_SAMPLES + 1, LEDGER_TOTAL + 1)],
        stored=[(LEDGER_DATE, LEDGER_SAMPLES, LEDGER_TOTAL)],
        **kw,
    )


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


# ── The scans the ledger now speaks for ───────────────────────────────────


def test_a_noop_run_does_not_scan_the_stop_route_keys():
    """The key scan reads every row the agency has, with no dep_delay filter.
    It can be skipped only because total_rows counts those same rows."""
    _, ch = _noop_run(has_static=True)
    assert ch.stop_routes_scans == []


def test_a_run_with_a_changed_date_scans_the_stop_route_keys_whole():
    _, ch = _changed_run(has_static=True)
    (scan,) = ch.stop_routes_scans
    assert "rebuild_dates" not in scan


def test_agg_stop_routes_is_skippable_but_never_date_scoped():
    """Keyed by stop, valued over all of history: a no-change run may leave
    it standing, but a changed date can only mean a whole rebuild."""
    assert "agg_stop_routes" in analyze_mod._NOOP_SKIPPABLE_AGG_TABLES
    assert "agg_stop_routes" not in analyze_mod._INCREMENTAL_AGG_TABLES


def test_a_noop_run_neither_purges_nor_rebuilds_agg_stop_routes():
    conn, _ = _noop_run(has_static=True)
    assert not [s for s in conn.executed if "DELETE FROM agg_stop_routes" in s]
    assert not [s for s in conn.executed if "INSERT INTO agg_stop_routes" in s]


# The scheduled headway's median reads the static schedule and nothing else.
_HEADWAY_MEDIAN = "PERCENTILE_DISC(0.5) WITHIN GROUP (ORDER BY headway_sec) AS scheduled_headway_median_sec"


def test_a_noop_run_neither_purges_nor_rebuilds_the_scheduled_headway():
    """Every column it reads is in the fingerprint, which a run with no
    changed date has just matched."""
    conn, _ = _noop_run(has_static=True)
    assert not [s for s in conn.executed if "DELETE FROM agg_route_headway WHERE" in s]
    assert not [s for s in conn.executed if _HEADWAY_MEDIAN in s]


def test_a_run_with_a_changed_date_rebuilds_the_scheduled_headway_whole():
    conn, _ = _changed_run(has_static=True)
    assert [s for s in conn.executed if "DELETE FROM agg_route_headway WHERE agency_id = %s" in s]
    assert [s for s in conn.executed if _HEADWAY_MEDIAN in s]


def test_the_scheduled_headway_reads_only_fingerprinted_columns():
    """Pins the columns the skip relies on as a known-good snapshot of
    _STATIC_DEPENDENCY_COLUMNS, not a check derived from the headway SQL
    itself -- a column the query reads that this snapshot omits would not
    fail this test."""
    covered = analyze_mod._STATIC_DEPENDENCY_COLUMNS
    assert {"trip_id", "route_id", "service_id"} <= set(covered["static_trips"])
    assert {"trip_id", "stop_id", "departure_time"} <= set(covered["static_stop_times"])
    assert "route_id" in covered["static_routes"]
    assert "agg_route_headway" in analyze_mod._NOOP_SKIPPABLE_AGG_TABLES


@pytest.mark.parametrize("table", ["agg_service_delivered_daily", "agg_schedule_revision_daily"])
def test_the_delayless_readers_are_incremental_now(table):
    assert table in analyze_mod._INCREMENTAL_AGG_TABLES


def test_a_noop_run_issues_no_service_delivered_or_schedule_revision_scan():
    _, ch = _noop_run(ingest_strategy="static_join")
    assert ch.service_delivered_builds == []
    assert ch.schedule_revision_builds == []


def test_a_changed_run_scopes_service_delivered_and_schedule_revision_to_the_changed_dates():
    _, ch = _changed_run(ingest_strategy="static_join")
    assert ch.service_delivered_builds and ch.schedule_revision_builds
    for sql, params in ch.service_delivered_builds + ch.schedule_revision_builds:
        assert sql.count("toDate(captured_at, 'Asia/Tokyo') IN {rebuild_dates:Array(Date)}") >= 1
        assert params["rebuild_dates"] == [LEDGER_DATE]
    # Every FROM updates in the cancellation builder is bounded, not just the first.
    ((sd_sql, _),) = ch.service_delivered_builds
    assert sd_sql.count("FROM updates") == sd_sql.count("{rebuild_dates:Array(Date)}")


def test_a_date_that_lost_only_delayless_rows_is_rebuilt():
    """raw_samples stands still; only total_rows moved."""
    conn, ch = _run(
        [(LEDGER_DATE, LEDGER_SAMPLES, LEDGER_TOTAL - 9)],
        stored=[(LEDGER_DATE, LEDGER_SAMPLES, LEDGER_TOTAL)],
        has_static=True,
    )
    assert len(ch.stop_routes_scans) == 1
    assert [s for s in conn.executed if "DELETE FROM agg_feed_health WHERE agency_id = %s AND date = ANY(%s)" in s]


def test_a_ledger_without_total_rows_rebuilds_every_date():
    """A row written before the column existed cannot vouch for delay-less rows."""
    conn, ch = _run(
        [(LEDGER_DATE, LEDGER_SAMPLES, LEDGER_TOTAL)],
        stored=[(LEDGER_DATE, LEDGER_SAMPLES, None)],
    )
    assert len(ch.alltime_scans) == 1
    assert [
        s for s in conn.executed if "DELETE FROM agg_daily_trend WHERE agency_id = %s" in s and "date = ANY" not in s
    ]


def test_the_ledger_query_counts_every_row_and_the_delay_rows_separately():
    _, ch = _noop_run()
    ledger_sql = next(sql for sql, _ in ch.queries if "raw_samples" in sql and "clamp_count" not in sql)
    assert "count() AS total_rows" in ledger_sql
    assert "countIf(dep_delay IS NOT NULL) AS raw_samples" in ledger_sql
    where = ledger_sql.split("GROUP BY")[0].split("WHERE")[1]
    assert "dep_delay" not in where, "a WHERE on dep_delay would hide delay-less rows from total_rows"


def test_feed_health_build_records_total_rows():
    _, ch = _changed_run()
    build_sql = next(sql for sql, _ in ch.queries if "clamp_count" in sql)
    assert "count() AS total_rows" in build_sql
    assert "dep_delay IS NOT NULL" not in build_sql.split("GROUP BY")[0].split("WHERE")[1]


def test_a_noop_run_still_records_this_build_in_agg_meta():
    """The record has to keep pace even when nothing was rebuilt: the
    fingerprint it carries is what the next run compares against."""
    conn, _ = _noop_run()

    assert [s for s in conn.executed if "INSERT INTO agg_meta" in s]


# ── The fingerprint that decides whether a date may be trusted ───────────


def test_the_fingerprint_changes_when_the_plausibility_clamp_changes(monkeypatch):
    """The clamp decides which rows reach every aggregate, so moving it makes
    every already-built date wrong while no row anywhere changed."""
    before = analyze_mod._static_fingerprint(AGENCY_ID, None, has_static=False, ingest_strategy=None)
    monkeypatch.setattr(analyze_mod, "MAX_PLAUSIBLE_DELAY_SEC", analyze_mod.MAX_PLAUSIBLE_DELAY_SEC + 1)

    assert analyze_mod._static_fingerprint(AGENCY_ID, None, has_static=False, ingest_strategy=None) != before


def test_the_fingerprint_changes_when_the_builder_logic_version_changes(monkeypatch):
    before = analyze_mod._static_fingerprint(AGENCY_ID, None, has_static=False, ingest_strategy=None)
    monkeypatch.setattr(analyze_mod, "ANALYZE_LOGIC_VERSION", analyze_mod.ANALYZE_LOGIC_VERSION + 1)

    assert analyze_mod._static_fingerprint(AGENCY_ID, None, has_static=False, ingest_strategy=None) != before


def test_the_fingerprint_changes_when_the_ingest_strategy_changes():
    """The strategy decides whether the RT-field aggregates are built at all,
    so an agency moving off static_join must not keep its old dates' rows."""
    before = analyze_mod._static_fingerprint(AGENCY_ID, None, has_static=False, ingest_strategy="static_join")

    assert analyze_mod._static_fingerprint(AGENCY_ID, None, has_static=False, ingest_strategy="gtfs_rt") != before


def test_a_strategy_change_alone_rebuilds_every_date():
    """No date gained a row, but the aggregates were built for another
    strategy: the old dates' service-delivered rows must not survive."""
    ledger = [(LEDGER_DATE, LEDGER_SAMPLES, LEDGER_TOTAL)]
    conn = FakeConn(ledger_rows=ledger, ingest_strategy="gtfs_rt")
    conn._fingerprint = analyze_mod._static_fingerprint(
        AGENCY_ID, conn, has_static=False, ingest_strategy="static_join"
    )
    ch = FakeClickHouse(ledger)
    analyze(AGENCY_ID, conn, ch)

    assert len(ch.alltime_scans) == 1
    assert [
        s
        for s in conn.executed
        if "DELETE FROM agg_service_delivered_daily WHERE agency_id = %s" in s and "ANY" not in s
    ]


def test_the_fingerprint_is_never_empty_without_a_static_schedule():
    """An agency with no schedule still has a clamp and a builder version to
    invalidate against; an empty value would leave it with neither."""
    assert analyze_mod._static_fingerprint(AGENCY_ID, None, has_static=False, ingest_strategy=None) != ""
