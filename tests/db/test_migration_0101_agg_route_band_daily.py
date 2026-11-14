"""agg_route_band_daily is an aggregate analyze rebuilds in full, so rolling it
back loses nothing a run cannot restore and its down needs no destructive mark."""

import os

import psycopg2

from db import migrate

_VERSION = "0101"
_DOWN = migrate._MIGRATIONS_DIR / f"{_VERSION}_agg_route_band_daily.down.sql"


def _indexes(conn) -> dict[str, list[str]]:
    """Each index on the table, by name, with its columns in order."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT c.relname, a.attname FROM pg_index i "
            "JOIN pg_class c ON c.oid = i.indexrelid "
            "JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey) "
            "WHERE i.indrelid = to_regclass('agg_route_band_daily') "
            "ORDER BY c.relname, array_position(i.indkey, a.attnum)"
        )
        out: dict[str, list[str]] = {}
        for index, column in cur.fetchall():
            out.setdefault(index, []).append(column)
    return out


_EXPECTED = {
    "agg_route_band_daily_pkey": ["agency_id", "date", "route_code", "time_band"],
    "idx_agg_route_band_daily_band": ["agency_id", "time_band", "date"],
}


def test_the_table_is_rebuildable_so_its_down_is_not_destructive():
    assert "agg_route_band_daily" in migrate.rebuildable_agg_tables()
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
