"""agg_route_segment_runtime is an aggregate analyze rebuilds in full, so rolling
it back loses nothing a run cannot restore and its down needs no destructive
mark. On a database with a retention floor, analyze fills it only from the floor
on, so there its up migration counts as losing data and waits to be named."""

import os

import psycopg2

from db import migrate

_VERSION = "0109"
_UP = migrate._MIGRATIONS_DIR / f"{_VERSION}_agg_route_segment_runtime.up.sql"
_DOWN = migrate._MIGRATIONS_DIR / f"{_VERSION}_agg_route_segment_runtime.down.sql"


def _indexes(conn) -> dict[str, str]:
    """Each index on the table, by name: its columns."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT c.relname, pg_get_indexdef(i.indexrelid) FROM pg_index i "
            "JOIN pg_class c ON c.oid = i.indexrelid "
            "WHERE i.indrelid = to_regclass('agg_route_segment_runtime')"
        )
        return {name: definition.split(" USING btree ", 1)[1] for name, definition in cur.fetchall()}


_EXPECTED = {
    "agg_route_segment_runtime_pkey": "(agency_id, date, route_code, from_stop_id, to_stop_id, time_band)",
    "idx_agg_route_segment_runtime_route": "(agency_id, route_code, date)",
}


def test_the_table_is_rebuildable_so_its_down_is_not_destructive():
    assert "agg_route_segment_runtime" in migrate.rebuildable_agg_tables()
    assert migrate.data_losing_tables(_DOWN.read_text()) == []
    assert not migrate.is_destructive_down(_DOWN.read_text())


def test_on_a_retention_floor_creating_the_table_counts_as_losing_data():
    assert migrate.data_losing_tables(_UP.read_text()) == []
    assert migrate.data_losing_tables(_UP.read_text(), retention_floor=True) == ["agg_route_segment_runtime"]


def test_rolling_back_drops_the_table_and_migrating_up_restores_it(apply_schema):
    below = max(version for version in migrate._versions_on_disk() if version < _VERSION)
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    try:
        assert _indexes(conn) == _EXPECTED
        migrate.migrate_down(below, conn, force_destructive=True)
        assert _indexes(conn) == {}

        migrate.migrate_up(conn)
        assert _indexes(conn) == _EXPECTED
    finally:
        conn.rollback()
        conn.close()
