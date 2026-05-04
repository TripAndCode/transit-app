import os

import psycopg2
import pytest

from db.migrate import migrate_down, migrate_up

DATABASE_URL = os.environ["DATABASE_URL"]


@pytest.fixture(scope="module", autouse=True)
def ensure_migrated():
    """Run migrate_up so schema_migrations is populated before any test in this module."""
    conn = psycopg2.connect(DATABASE_URL)
    migrate_up(conn)
    conn.close()


def test_migrate_up_creates_schema_migrations_table(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'schema_migrations' ORDER BY column_name
        """)
        cols = {r[0] for r in cur.fetchall()}
    assert {"version", "applied_at"} <= cols


def test_migrate_up_records_all_versions(pg_conn):
    """Every up migration on disk must have a row in schema_migrations.

    Asserting against the disk listing instead of a hardcoded list means
    new migrations don't break this test.
    """
    from db.migrate import _versions_on_disk

    expected = sorted(_versions_on_disk())
    with pg_conn.cursor() as cur:
        cur.execute("SELECT version FROM schema_migrations ORDER BY version")
        versions = [r[0] for r in cur.fetchall()]
    assert versions == expected


def test_migrate_up_idempotent(pg_conn):
    conn = psycopg2.connect(DATABASE_URL)
    try:
        migrate_up(conn)  # already up to date — should print message and return
    finally:
        conn.close()


def test_migrate_down_and_up(pg_conn):
    """Round-trip: roll back to "0002", then migrate_up restores everything.

    The previous version pinned itself to "0003 is the latest" and broke
    every time a new migration landed (and worse, left the schema
    half-applied for downstream tests). Using target="0002" keeps the
    rollback deterministic regardless of how many migrations exist
    above it; the unconditional migrate_up in `finally` ensures a clean
    schema even if the assertions fail.
    """
    conn = psycopg2.connect(DATABASE_URL)
    try:
        migrate_down("0002", conn)  # roll back everything above 0002
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name='api_keys'")
            assert cur.fetchone() is None, "api_keys (0003) should be gone after rollback"
            cur.execute("SELECT version FROM schema_migrations ORDER BY version")
            versions = [r[0] for r in cur.fetchall()]
        assert versions == ["0001", "0002"]
    finally:
        # Always restore the schema even if the assertions above failed,
        # so downstream tests in the session see a fully-migrated DB.
        migrate_up(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name='api_keys'")
            assert cur.fetchone() is not None, "api_keys table should exist after migrate_up"
        conn.close()
