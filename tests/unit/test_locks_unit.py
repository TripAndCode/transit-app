"""Pure-logic tests for pipeline.locks.lock_agency_ingest_or_timeout.

DB-free: a fake connection/cursor records the SQL issued instead of talking
to Postgres. The blocking behavior itself (an actual wait) is only
observable against a real server and is covered by the DB-backed tests in
tests/pipeline/test_locks.py.
"""

import psycopg2.errors
import pytest

from pipeline.locks import INGEST_ANALYZE_LOCK_KEY, lock_agency_ingest_or_timeout


class FakeCursor:
    def __init__(self, raise_on_lock_sql: bool):
        self.executed: list[tuple[str, tuple | None]] = []
        self._raise_on_lock_sql = raise_on_lock_sql

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        if "pg_advisory_lock" in sql and self._raise_on_lock_sql:
            raise psycopg2.errors.LockNotAvailable("canceling statement due to lock timeout")

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class FakeConn:
    def __init__(self, raise_on_lock_sql: bool = False):
        self._cursor = FakeCursor(raise_on_lock_sql)
        self.committed = False
        self.rolled_back = False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    @property
    def executed(self):
        return self._cursor.executed


def test_sets_lock_timeout_before_the_blocking_acquire():
    conn = FakeConn()
    lock_agency_ingest_or_timeout(conn, agency_id=5, timeout_seconds=20)

    statements = [sql for sql, _ in conn.executed]
    assert any("set local lock_timeout" in s.lower() for s in statements)
    timeout_idx = next(i for i, s in enumerate(statements) if "lock_timeout" in s.lower())
    lock_idx = next(i for i, s in enumerate(statements) if "pg_advisory_lock" in s.lower())
    assert timeout_idx < lock_idx


def test_lock_timeout_value_is_passed_as_a_parameter():
    conn = FakeConn()
    lock_agency_ingest_or_timeout(conn, agency_id=5, timeout_seconds=20)

    _sql, params = next((s, p) for s, p in conn.executed if "lock_timeout" in s.lower())
    assert params == ("20s",)


def test_uses_the_blocking_form_not_the_non_blocking_try_variant():
    conn = FakeConn()
    lock_agency_ingest_or_timeout(conn, agency_id=5, timeout_seconds=20)

    sql, params = next((s, p) for s, p in conn.executed if "advisory_lock" in s.lower())
    assert "pg_try_advisory_lock" not in sql
    assert "pg_advisory_lock" in sql
    assert params == (INGEST_ANALYZE_LOCK_KEY, 5)


def test_returns_true_and_commits_when_acquired():
    conn = FakeConn(raise_on_lock_sql=False)
    assert lock_agency_ingest_or_timeout(conn, agency_id=5, timeout_seconds=20) is True
    assert conn.committed is True
    assert conn.rolled_back is False


def test_returns_false_and_rolls_back_on_lock_timeout():
    conn = FakeConn(raise_on_lock_sql=True)
    assert lock_agency_ingest_or_timeout(conn, agency_id=5, timeout_seconds=20) is False
    assert conn.rolled_back is True
    assert conn.committed is False


def test_propagates_other_database_errors():
    class ExplodingCursor(FakeCursor):
        def execute(self, sql, params=None):
            if "pg_advisory_lock" in sql:
                raise psycopg2.errors.UndefinedTable("boom")
            super().execute(sql, params)

    conn = FakeConn()
    conn._cursor = ExplodingCursor(raise_on_lock_sql=False)
    with pytest.raises(psycopg2.errors.UndefinedTable):
        lock_agency_ingest_or_timeout(conn, agency_id=5, timeout_seconds=20)
