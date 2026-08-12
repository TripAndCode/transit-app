"""DB-backed tests for pipeline.locks -- the cross-process ingest/analyze
advisory lock shared by api/routers/internal.py's cron endpoint and
gtfs_pipeline.py's ingest/ingest_live/analyze/analyze_all CLI commands."""

import os

import psycopg2

from pipeline.locks import INGEST_ANALYZE_LOCK_KEY, try_lock_ingest_analyze

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://localhost/transit")


def test_try_lock_ingest_analyze_acquires_when_free(pg_conn):
    assert try_lock_ingest_analyze(pg_conn) is True


def test_try_lock_ingest_analyze_returns_false_when_held_by_another_session(pg_conn):
    """A second connection holding the lock must get False back immediately
    (non-blocking), not hang waiting for the first to release."""
    holder = psycopg2.connect(DATABASE_URL)
    holder.autocommit = True
    try:
        with holder.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", (INGEST_ANALYZE_LOCK_KEY,))
            assert cur.fetchone()[0] is True

        assert try_lock_ingest_analyze(pg_conn) is False
    finally:
        with holder.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(%s)", (INGEST_ANALYZE_LOCK_KEY,))
        holder.close()


def test_try_lock_ingest_analyze_releases_on_connection_close(pg_conn):
    """Session-level, not explicitly unlocked: closing the holding
    connection must release the lock for the next acquirer -- this is the
    property both api/routers/internal.py and gtfs_pipeline.py rely on
    instead of an explicit pg_advisory_unlock call."""
    holder = psycopg2.connect(DATABASE_URL)
    holder.autocommit = True
    with holder.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s)", (INGEST_ANALYZE_LOCK_KEY,))
        assert cur.fetchone()[0] is True
    holder.close()

    assert try_lock_ingest_analyze(pg_conn) is True


def test_try_lock_ingest_analyze_survives_txn_rollback(pg_conn):
    """The lock is session-scoped, not transaction-scoped: it must still be
    held after the acquiring transaction rolls back -- this is exactly the
    shape callers rely on (pg_try_advisory_lock is called, then later
    statements in other transactions on the same connection run, some of
    which may roll back without releasing the lock). No explicit unlock
    needed: pg_conn's own teardown closes the connection, which releases
    every session-level advisory lock it holds -- the same property
    test_try_lock_ingest_analyze_releases_on_connection_close asserts."""
    assert try_lock_ingest_analyze(pg_conn) is True
    with pg_conn.cursor() as cur:
        cur.execute("SELECT 1")
    pg_conn.rollback()

    holder = psycopg2.connect(DATABASE_URL)
    holder.autocommit = True
    try:
        with holder.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", (INGEST_ANALYZE_LOCK_KEY,))
            assert cur.fetchone()[0] is False  # still held by pg_conn
    finally:
        holder.close()
