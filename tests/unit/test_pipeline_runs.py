"""`pipeline.runs` — the bookkeeping every pipeline job writes its own row through.

Two properties dominate these tests, because both are the difference between
a timeline that can be trusted and one that quietly lies:

- **Recording never breaks the job.** An environment whose schema predates
  `pipeline_runs`, or a connection that has already gone away, must cost the
  run its bookkeeping row and nothing else.
- **The row survives the work's own rollback.** `analyze()` rolls its
  transaction back before re-raising, so an error row written inside that
  transaction would be erased exactly when it matters. Each write therefore
  commits on its own.

DB-free: the connection is a stub that records the statements it was given.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from pipeline.runs import (
    MAX_ERROR_CHARS,
    RUN_KINDS,
    RUN_STATUSES,
    finish_run,
    reap_abandoned_runs,
    record_run,
    start_run,
)


class _Cursor:
    def __init__(self, conn, result):
        self.conn = conn
        self._result = result
        self.rowcount = conn.rowcount

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        if self.conn.fail_on is not None and self.conn.fail_on in sql:
            self.conn.broken = True
            raise RuntimeError("relation does not exist")
        self.conn.statements.append((" ".join(sql.split()), params))

    def fetchone(self):
        return self._result


class _Conn:
    """Stub psycopg2 connection recording normalized SQL and commit order."""

    def __init__(self, *, run_id=7, fail_on=None, rowcount=0):
        self.statements: list[tuple[str, object]] = []
        self.events: list[str] = []
        self.run_id = run_id
        self.fail_on = fail_on
        self.rowcount = rowcount
        self.broken = False

    def cursor(self):
        return _Cursor(self, (self.run_id,))

    def commit(self):
        self.events.append("commit")

    def rollback(self):
        self.events.append("rollback")
        self.broken = False


def test_kind_and_status_vocabularies_match_the_table_constraints():
    assert RUN_KINDS == ("ingest", "analyze", "weather", "static")
    assert RUN_STATUSES == ("running", "ok", "skipped", "error")


def test_start_run_inserts_a_running_row_and_returns_its_id():
    conn = _Conn(run_id=42)
    assert start_run(conn, "analyze", agency_id=3, requested_by=9) == 42
    sql, params = conn.statements[0]
    assert sql.startswith("INSERT INTO pipeline_runs")
    assert "RETURNING run_id" in sql
    assert params == ("analyze", 3, "running", None, 9)
    assert conn.events == ["commit"]


def test_a_skipped_run_is_recorded_whole_with_its_lock_wait():
    conn = _Conn()
    assert start_run(conn, "ingest", agency_id=None, lock_wait_ms=12, status="skipped") == 7
    _, params = conn.statements[0]
    assert params == ("ingest", None, "skipped", 12, None)


def test_start_run_rejects_a_kind_the_table_would_reject():
    with pytest.raises(ValueError):
        start_run(_Conn(), "vacuum")


def test_finish_run_closes_the_row_with_its_outcome_and_row_count():
    conn = _Conn()
    finish_run(conn, 42, "ok", rows=1234)
    sql, params = conn.statements[0]
    assert sql.startswith("UPDATE pipeline_runs SET")
    assert "finished_at = now()" in sql
    assert params == ("ok", 1234, None, 42)
    assert conn.events == ["commit"]


def test_finish_run_truncates_an_unbounded_error_so_one_traceback_cannot_bloat_the_row():
    conn = _Conn()
    finish_run(conn, 1, "error", error="x" * (MAX_ERROR_CHARS + 500))
    _, params = conn.statements[0]
    assert len(params[2]) == MAX_ERROR_CHARS


def test_finish_run_on_a_run_that_was_never_recorded_is_a_no_op():
    conn = _Conn()
    finish_run(conn, None, "ok")
    assert conn.statements == []


def test_a_schema_without_the_table_costs_the_bookkeeping_row_and_nothing_else():
    conn = _Conn(fail_on="INSERT INTO pipeline_runs")
    assert start_run(conn, "ingest") is None
    # Rolled back, so the caller's connection is usable again rather than
    # stuck in a failed transaction for the rest of the job.
    assert conn.events == ["rollback"]
    assert conn.broken is False


def test_record_run_marks_a_completed_job_ok_with_the_rows_it_reported():
    conn = _Conn(run_id=5)
    with record_run(conn, "ingest", agency_id=1) as run:
        run.rows = 99
    assert [params for _, params in conn.statements] == [
        ("ingest", 1, "running", None, None),
        ("ok", 99, None, 5),
    ]


def test_record_run_marks_a_failed_job_error_and_re_raises():
    conn = _Conn(run_id=5)
    with pytest.raises(ZeroDivisionError):
        with record_run(conn, "analyze", agency_id=2):
            raise ZeroDivisionError("boom")
    status, _rows, error, run_id = conn.statements[1][1]
    assert (status, run_id) == ("error", 5)
    assert "ZeroDivisionError" in error and "boom" in error
    # The work's own failure may have left the transaction aborted; the error
    # row is only writable once that is cleared.
    assert conn.events == ["commit", "rollback", "commit"]


def test_record_run_still_runs_the_work_when_the_row_could_not_be_opened():
    conn = _Conn(fail_on="INSERT INTO pipeline_runs")
    ran = False
    with record_run(conn, "weather"):
        ran = True
    assert ran is True
    assert conn.statements == []


# ── Reaping runs nothing will ever close ──────────────────────────────────

_NOW = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)


def test_the_reaper_closes_only_rows_still_open_past_the_cutoff():
    conn = _Conn(rowcount=3)
    assert reap_abandoned_runs(conn, older_than=timedelta(hours=2), now=_NOW) == 3
    sql, params = conn.statements[0]
    assert sql == (
        "UPDATE pipeline_runs SET status = 'error', error = 'abandoned', finished_at = %s "
        "WHERE status = 'running' AND finished_at IS NULL AND started_at < %s"
    )
    # finished_at is the reap moment, not the cutoff: the row says when it
    # was given up on, and the cutoff only decides which rows qualify.
    assert params == (_NOW, _NOW - timedelta(hours=2))
    assert conn.events == ["commit"]


def test_the_reaper_defaults_to_a_two_hour_grace_so_a_long_sweep_is_left_alone():
    conn = _Conn()
    reap_abandoned_runs(conn, now=_NOW)
    _, params = conn.statements[0]
    assert params == (_NOW, _NOW - timedelta(hours=2))


def test_the_reaper_dates_itself_when_no_clock_is_supplied():
    conn = _Conn()
    before = datetime.now(timezone.utc)
    reap_abandoned_runs(conn)
    _, params = conn.statements[0]
    assert before <= params[0] <= datetime.now(timezone.utc)


def test_a_schema_without_the_table_costs_the_reap_and_nothing_else():
    conn = _Conn(fail_on="UPDATE pipeline_runs")
    assert reap_abandoned_runs(conn, now=_NOW) == 0
    assert conn.events == ["rollback"]
    assert conn.broken is False


def test_a_stored_error_never_carries_the_feed_credential_that_caused_it():
    conn = _Conn(run_id=5)
    with pytest.raises(RuntimeError):
        with record_run(conn, "ingest", agency_id=1):
            raise RuntimeError("fetch failed: https://user:pass@feeds.test/rt.pb?apikey=SECRET")
    error = conn.statements[1][1][2]
    assert "SECRET" not in error
    assert "user:pass" not in error
    assert "https://feeds.test/rt.pb" in error
