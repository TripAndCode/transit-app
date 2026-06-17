"""Migration-drift detection (transit_test only)."""

import os

import psycopg2
import pytest

from db.migrate import _versions_on_disk, migrate_up, pending_migrations

DATABASE_URL = os.environ["DATABASE_URL"]


@pytest.fixture(scope="module", autouse=True)
def ensure_migrated():
    conn = psycopg2.connect(DATABASE_URL)
    migrate_up(conn)
    conn.close()


def test_no_pending_when_fully_migrated(pg_conn):
    assert pending_migrations(pg_conn) == []


def test_deleting_a_version_makes_it_pending(pg_conn):
    latest = _versions_on_disk()[-1]
    with pg_conn.cursor() as cur:
        cur.execute("DELETE FROM schema_migrations WHERE version = %s", (latest,))
    assert pending_migrations(pg_conn) == [latest]
    pg_conn.rollback()
    assert pending_migrations(pg_conn) == []


def test_missing_tracking_table_means_all_pending(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute("DROP TABLE schema_migrations")
        result = pending_migrations(pg_conn)
    assert result == _versions_on_disk()
    pg_conn.rollback()
    assert pending_migrations(pg_conn) == []
