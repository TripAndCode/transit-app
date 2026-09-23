"""DB-backed tests for pipeline.locks -- the cross-process ingest/analyze
advisory lock shared by api/routers/internal.py's cron endpoint and
gtfs_pipeline.py's ingest/ingest_live/analyze/analyze_all CLI commands, plus
the per-agency `updates` lock the collector push endpoint and analyze() both
take."""

import os
import time

import psycopg2

from pipeline.locks import (
    INGEST_ANALYZE_LOCK_KEY,
    agency_ingest_lock,
    try_lock_agency_ingest,
    try_lock_ingest_analyze,
)

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

    # `close()` returns once the client socket is gone, but the server frees a
    # session's advisory locks while tearing its backend down, which happens
    # afterwards. Retry until it does: asserting once, immediately, races that
    # teardown and fails whenever the acquirer wins. A caller cannot observe an
    # instantaneous release either, so "released within a bounded wait" is the
    # property being relied on, and a release that never lands still fails here.
    deadline = time.monotonic() + 10.0
    while True:
        if try_lock_ingest_analyze(pg_conn) is True:
            break
        assert time.monotonic() < deadline, "lock still held 10s after the holding connection closed"
        time.sleep(0.05)


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


# Every test below unlocks explicitly instead of leaning on connection
# teardown, which the release-on-close test above deliberately waits out: the
# server frees a session's advisory locks only while tearing its backend down,
# *after* close() returns. Tests sharing this long-lived database (see
# transit-app-gotchas on the fixed-port pair) otherwise race that teardown --
# a later test taking the same key fails intermittently, for no reason visible
# in its own body.
def _unlock_agency(conn, agency_id: int) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_unlock(%s, %s)", (INGEST_ANALYZE_LOCK_KEY, agency_id))


def _unlock_global(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_unlock(%s)", (INGEST_ANALYZE_LOCK_KEY,))


def test_try_lock_agency_ingest_does_not_serialize_different_agencies(pg_conn):
    """The whole point of the per-agency key: one agency's collector push must
    not 409 another's. A single global key made every concurrent arrival
    across the fleet contend, which dropped the losing poll."""
    other = psycopg2.connect(DATABASE_URL)
    other.autocommit = True
    try:
        assert try_lock_agency_ingest(pg_conn, 1) is True
        assert try_lock_agency_ingest(other, 2) is True
    finally:
        _unlock_agency(pg_conn, 1)
        other.close()


def test_try_lock_agency_ingest_still_excludes_same_agency(pg_conn):
    """Narrower scope, not absent exclusion: a retry of the same agency's push
    (the case ingest_live_payload's recent_file_name_exists check cannot make
    atomic on its own) must still be serialized."""
    other = psycopg2.connect(DATABASE_URL)
    other.autocommit = True
    try:
        assert try_lock_agency_ingest(pg_conn, 7) is True
        assert try_lock_agency_ingest(other, 7) is False
    finally:
        _unlock_agency(pg_conn, 7)
        other.close()


def test_agency_and_global_locks_occupy_separate_spaces(pg_conn):
    """Postgres keeps one-argument and two-argument advisory locks in separate
    spaces. This is the reason analyze() has to take the agency's two-argument
    key itself (see agency_ingest_lock) rather than relying on the global key
    its callers already hold -- holding domain 1 excludes nothing in domain 2.
    Asserted so that a future reader deleting analyze()'s lock as "redundant
    with the global one" fails here instead of shipping the skew.

    The agency argument is the global key's own value on purpose: if these two
    ever shared a space, that is the one agency id that would collide, so it
    is the case most likely to catch a regression here."""
    other = psycopg2.connect(DATABASE_URL)
    other.autocommit = True
    try:
        assert try_lock_ingest_analyze(pg_conn) is True
        assert try_lock_agency_ingest(other, INGEST_ANALYZE_LOCK_KEY) is True
    finally:
        _unlock_global(pg_conn)
        other.close()


def test_agency_ingest_lock_blocks_a_concurrent_append_for_that_agency(pg_conn):
    """The invariant analyze() depends on: while it holds an agency's key, no
    append for that agency can land. analyze() reads `updates` at more than one
    point per run, and a write landing between those reads leaves the ledger
    newer than the aggregates it certifies -- a skew _dates_needing_rebuild
    cannot see, because it is the ledger it compares against."""
    other = psycopg2.connect(DATABASE_URL)
    other.autocommit = True
    try:
        with agency_ingest_lock(pg_conn, 11):
            assert try_lock_agency_ingest(other, 11) is False
            # A different agency is still free -- exclusion is per agency, so
            # analyzing one does not stall the rest of the fleet's pushes.
            assert try_lock_agency_ingest(other, 12) is True
            _unlock_agency(other, 12)
    finally:
        other.close()


def test_agency_ingest_lock_releases_on_block_exit(pg_conn):
    """Released on the way out, not at connection close: one long-lived
    connection analyzes every agency in turn, so a lock surviving the block
    would keep blocking that agency's pushes for the rest of the fleet's run."""
    with agency_ingest_lock(pg_conn, 13):
        pass
    other = psycopg2.connect(DATABASE_URL)
    other.autocommit = True
    try:
        assert try_lock_agency_ingest(other, 13) is True
        _unlock_agency(other, 13)
    finally:
        other.close()


def test_agency_ingest_lock_releases_when_the_block_raises(pg_conn):
    """analyze() propagates after rolling back; the lock must not outlive that
    failure, or one failed agency would block its pushes until the process
    exits."""
    other = psycopg2.connect(DATABASE_URL)
    other.autocommit = True
    try:
        try:
            with agency_ingest_lock(pg_conn, 14):
                raise RuntimeError("analyze failed mid-run")
        except RuntimeError:
            pass
        assert try_lock_agency_ingest(other, 14) is True
        _unlock_agency(other, 14)
    finally:
        other.close()
