"""static_version_contents mirrors, per agency and version name, the content
ClickHouse's static_version_map last maps it to. Rolling back loses the rows,
which the fill script rebuilds from the map, so the down is marked
destructive."""

import os
from datetime import datetime, timezone

import psycopg2
import pytest

from db import migrate

_VERSION = "0097"


def _present(conn) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('static_version_contents') IS NOT NULL")
        return cur.fetchone()[0]


def test_the_down_is_marked_destructive():
    down = migrate._MIGRATIONS_DIR / f"{_VERSION}_static_version_contents.down.sql"
    assert migrate.is_destructive_down(down.read_text())


def test_a_name_holds_one_content(pg_conn, agency_id):
    insert = (
        "INSERT INTO static_version_contents (agency_id, static_version_id, content_hash, archived_at) "
        "VALUES (%s, 'gtfs_static_20261025', %s, %s)"
    )
    with pg_conn.cursor() as cur:
        cur.execute(insert, (agency_id, "a" * 32, datetime(2026, 10, 25, tzinfo=timezone.utc)))
        with pytest.raises(psycopg2.errors.UniqueViolation):
            cur.execute(insert, (agency_id, "b" * 32, datetime(2026, 10, 26, tzinfo=timezone.utc)))


def test_rolling_back_drops_it_and_migrating_up_restores_it(apply_schema):
    below = max(version for version in migrate._versions_on_disk() if version < _VERSION)
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    try:
        assert _present(conn)
        migrate.migrate_down(below, conn, force_destructive=True)
        assert not _present(conn)
        migrate.migrate_up(conn)
        assert _present(conn)
    finally:
        conn.rollback()
        conn.close()
