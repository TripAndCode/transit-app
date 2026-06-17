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


def test_cmd_check_migrations_clean_when_current():
    import gtfs_pipeline

    # DATABASE_URL points at the migrated :5544 → no pending → returns cleanly
    gtfs_pipeline.cmd_check_migrations(object())  # args unused


def test_cmd_check_migrations_exits_when_behind(monkeypatch):
    import db.migrate
    import gtfs_pipeline

    monkeypatch.setattr(db.migrate, "pending_migrations", lambda conn: ["9999"])
    with pytest.raises(SystemExit) as ei:
        gtfs_pipeline.cmd_check_migrations(object())
    assert ei.value.code == 1
