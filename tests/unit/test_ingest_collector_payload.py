"""Unit tests for api.routers.internal._ingest_collector_payload.

DB-free: a fake psycopg2 connection/cursor records the SQL issued instead of
talking to Postgres; pipeline.clickhouse.get_client and
pipeline.ingest.ingest_live_payload are mocked. Covers two collector-push
findings: the lock wait (bounded, not an instant 409) and cleanup (the
ClickHouse client must close even if closing the Postgres connection raises).
"""

from unittest.mock import MagicMock, patch

import psycopg2.errors
import pytest
from fastapi import HTTPException

from api.routers.internal import COLLECTOR_LOCK_WAIT_SECONDS, _ingest_collector_payload


class FakeCursor:
    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=None):
        self._conn.executed.append((sql, params))
        normalized = " ".join(sql.split()).lower()
        if normalized.startswith("select 1 from agencies"):
            self._conn._last_fetchone = (1,) if self._conn.agency_exists else None
        elif "pg_advisory_lock" in normalized:
            if self._conn.raise_lock_timeout:
                raise psycopg2.errors.LockNotAvailable("canceling statement due to lock timeout")
            self._conn._last_fetchone = (True,)
        else:
            self._conn._last_fetchone = None

    def fetchone(self):
        return self._conn._last_fetchone

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class FakeConn:
    def __init__(self, *, agency_exists=True, raise_lock_timeout=False, raise_on_close=False):
        self.executed: list[tuple[str, tuple | None]] = []
        self.autocommit = None
        self.autocommit_history: list[bool] = []
        self.committed = False
        self.rolled_back = False
        self.closed = False
        self.agency_exists = agency_exists
        self.raise_lock_timeout = raise_lock_timeout
        self.raise_on_close = raise_on_close
        self._last_fetchone = None

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True
        if self.raise_on_close:
            raise RuntimeError("boom closing postgres connection")


def _patched(conn, ch_client, ingest_return=42):
    return (
        patch("psycopg2.connect", return_value=conn),
        patch("pipeline.clickhouse.get_client", return_value=ch_client),
        patch("pipeline.ingest.ingest_live_payload", return_value=ingest_return),
    )


def test_lock_wait_uses_bounded_timeout_and_the_blocking_sql(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake")
    conn = FakeConn()
    ch_client = MagicMock()
    p1, p2, p3 = _patched(conn, ch_client)
    with p1, p2, p3:
        result = _ingest_collector_payload(1, b"raw", "2026-09-19T12:00:00+00:00", "oracle/x")

    assert result == 42
    statements = [sql for sql, _ in conn.executed]
    timeout_stmt = next(s for s in statements if "lock_timeout" in s.lower())
    lock_stmt = next(s for s in statements if "advisory_lock" in s.lower())
    assert statements.index(timeout_stmt) < statements.index(lock_stmt)
    assert "pg_try_advisory_lock" not in lock_stmt
    assert "pg_advisory_lock" in lock_stmt

    timeout_params = next(p for s, p in conn.executed if "lock_timeout" in s.lower())
    assert timeout_params == (f"{COLLECTOR_LOCK_WAIT_SECONDS}s",)


def test_lock_wait_timeout_returns_409_same_as_the_old_instant_miss(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake")
    conn = FakeConn(raise_lock_timeout=True)
    ch_client = MagicMock()
    p1, p2, p3 = _patched(conn, ch_client)
    with p1, p2, p3 as mock_ingest:
        with pytest.raises(HTTPException) as exc_info:
            _ingest_collector_payload(1, b"raw", "2026-09-19T12:00:00+00:00", "oracle/x")

    assert exc_info.value.status_code == 409
    mock_ingest.assert_not_called()
    assert conn.rolled_back is True
    assert conn.closed is True


def test_ch_client_still_closed_when_postgres_close_raises(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake")
    conn = FakeConn(raise_on_close=True)
    ch_client = MagicMock()
    p1, p2, p3 = _patched(conn, ch_client)
    with p1, p2, p3:
        with pytest.raises(RuntimeError, match="boom closing postgres connection"):
            _ingest_collector_payload(1, b"raw", "2026-09-19T12:00:00+00:00", "oracle/x")

    assert conn.closed is True
    ch_client.close.assert_called_once()


def test_unknown_agency_still_closes_both_clients(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake")
    conn = FakeConn(agency_exists=False)
    ch_client = MagicMock()
    p1, p2, p3 = _patched(conn, ch_client)
    with p1, p2, p3:
        with pytest.raises(ValueError, match="Unknown or deleted agency_id"):
            _ingest_collector_payload(1, b"raw", "2026-09-19T12:00:00+00:00", "oracle/x")

    assert conn.closed is True
    ch_client.close.assert_not_called()
