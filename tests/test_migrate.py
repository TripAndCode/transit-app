import os
import psycopg2
import pytest
from db.migrate import migrate_up, migrate_down

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
    with pg_conn.cursor() as cur:
        cur.execute("SELECT version FROM schema_migrations ORDER BY version")
        versions = [r[0] for r in cur.fetchall()]
    assert versions == ["0001", "0002", "0003", "0004"]


def test_migrate_up_idempotent(pg_conn):
    conn = psycopg2.connect(DATABASE_URL)
    try:
        migrate_up(conn)  # already up to date — should print message and return
    finally:
        conn.close()


def test_migrate_down_and_up(pg_conn):
    conn = psycopg2.connect(DATABASE_URL)
    try:
        migrate_down(None, conn)  # rolls back latest (0004 snapshots)
        with conn.cursor() as cur:
            cur.execute("""
                SELECT table_name FROM information_schema.tables
                WHERE table_schema='public' AND table_name='snapshots'
            """)
            assert cur.fetchone() is None, "snapshots table should be gone after rollback"
            cur.execute("SELECT version FROM schema_migrations ORDER BY version")
            versions = [r[0] for r in cur.fetchall()]
        assert versions == ["0001", "0002", "0003"]
        migrate_up(conn)  # restore — re-applies 0004
        with conn.cursor() as cur:
            cur.execute("""
                SELECT table_name FROM information_schema.tables
                WHERE table_schema='public' AND table_name='snapshots'
            """)
            assert cur.fetchone() is not None, "snapshots table should exist after migrate_up"
    finally:
        conn.close()
