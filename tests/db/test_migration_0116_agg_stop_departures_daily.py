"""agg_stop_departures_daily is an aggregate analyze rebuilds in full, so rolling
it back loses nothing a run cannot restore and its down needs no destructive mark."""

import os

import psycopg2
import psycopg2.errors
import pytest

from db import migrate

_VERSION = "0116"
_DOWN = migrate._MIGRATIONS_DIR / f"{_VERSION}_agg_stop_departures_daily.down.sql"


def _indexes(conn) -> dict[str, str]:
    """Each index on the table, by name: its columns."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT c.relname, pg_get_indexdef(i.indexrelid) FROM pg_index i "
            "JOIN pg_class c ON c.oid = i.indexrelid "
            "WHERE i.indrelid = to_regclass('agg_stop_departures_daily')"
        )
        return {name: definition.split(" USING btree ", 1)[1] for name, definition in cur.fetchall()}


_EXPECTED = {
    "agg_stop_departures_daily_pkey": "(agency_id, stop_id, date)",
    "idx_agg_stop_departures_daily_agency_date": "(agency_id, date)",
}


def test_the_table_is_rebuildable_so_its_down_is_not_destructive():
    assert "agg_stop_departures_daily" in migrate.rebuildable_agg_tables()
    assert migrate.data_losing_tables(_DOWN.read_text()) == []
    assert not migrate.is_destructive_down(_DOWN.read_text())


def test_rolling_back_drops_the_table_and_migrating_up_restores_it(apply_schema):
    below = max(version for version in migrate._versions_on_disk() if version < _VERSION)
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    try:
        assert _indexes(conn) == _EXPECTED
        migrate.migrate_down(below, conn)
        assert _indexes(conn) == {}

        migrate.migrate_up(conn)
        assert _indexes(conn) == _EXPECTED
    finally:
        conn.rollback()
        conn.close()


def test_a_day_whose_arrays_differ_in_length_is_refused(pg_conn, agency_id):
    with pg_conn.cursor() as cur, pytest.raises(psycopg2.errors.CheckViolation):
        cur.execute(
            "INSERT INTO agg_stop_departures_daily VALUES (%s, 'S1', '2026-09-14', '{1,2}', '{0}')", (agency_id,)
        )
    pg_conn.rollback()
