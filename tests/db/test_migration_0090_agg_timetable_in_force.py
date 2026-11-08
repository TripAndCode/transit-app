"""agg_timetable_in_force records the versions in force each analyze built
against. Rolling it back loses that record, so its down is marked destructive,
and analyze refuses to run until it is applied again."""

import os

import psycopg2

from db import migrate

_VERSION = "0090"


def _exists(conn) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('agg_timetable_in_force') IS NOT NULL")
        return cur.fetchone()[0]


def test_the_down_is_marked_destructive():
    down = migrate._MIGRATIONS_DIR / f"{_VERSION}_agg_timetable_in_force.down.sql"
    assert migrate.is_destructive_down(down.read_text())


def test_rolling_back_drops_the_record_and_migrating_up_restores_it(apply_schema):
    below = max(version for version in migrate._versions_on_disk() if version < _VERSION)
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    try:
        assert _exists(conn)
        migrate.migrate_down(below, conn, force_destructive=True)
        assert not _exists(conn)

        migrate.migrate_up(conn)
        assert _exists(conn)
    finally:
        conn.rollback()
        conn.close()
