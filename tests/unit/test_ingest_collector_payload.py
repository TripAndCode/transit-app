"""Unit tests for api.routers.internal._ingest_collector_payload.

Covers cleanup (the ClickHouse client must close even if closing the Postgres
connection raises) and that a push waits on no lock.

DB-free: a fake psycopg2 connection/cursor records the SQL issued instead of
talking to Postgres; pipeline.clickhouse.get_client and
pipeline.ingest.ingest_live_payload are mocked.
"""

from unittest.mock import MagicMock, patch

import pytest

from api.routers.internal import _ingest_collector_payload


class FakeCursor:
    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=None):
        self._conn.executed.append((sql, params))
        normalized = " ".join(sql.split()).lower()
        if normalized.startswith("select 1 from agencies"):
            self._conn._last_fetchone = (1,) if self._conn.agency_exists else None
        else:
            self._conn._last_fetchone = None

    def fetchone(self):
        return self._conn._last_fetchone

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class FakeConn:
    def __init__(self, *, agency_exists=True, raise_on_close=False):
        self.executed: list[tuple[str, tuple | None]] = []
        self.autocommit = None
        self.autocommit_history: list[bool] = []
        self.committed = False
        self.rolled_back = False
        self.closed = False
        self.agency_exists = agency_exists
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


def test_a_push_takes_no_advisory_lock(monkeypatch):
    """A push writes updates_live, which analyze never reads, so it cannot skew
    analyze's per-date ledger and has nothing to wait for."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake")
    conn = FakeConn()
    p1, p2, p3 = _patched(conn, MagicMock())
    with p1, p2, p3 as mock_ingest:
        assert _ingest_collector_payload(1, b"raw", "2026-09-19T12:00:00+00:00", "oracle/x") == 42
    mock_ingest.assert_called_once()
    assert not any("advisory_lock" in s.lower() or "lock_timeout" in s.lower() for s, _ in conn.executed)


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
