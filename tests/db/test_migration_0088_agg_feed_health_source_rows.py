"""agg_feed_health counts each date's rows by ingest source, in two columns
the next analyze fills, so rolling them back loses nothing analyze cannot
rewrite."""

import os

import psycopg2

from db import migrate

_COLUMNS = {"archive_rows", "live_rows"}


def test_the_down_is_not_marked_destructive():
    down = migrate._MIGRATIONS_DIR / "0088_agg_feed_health_source_rows.down.sql"
    assert not migrate.is_destructive_down(down.read_text())


def _source_columns(conn) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT column_name, is_nullable FROM information_schema.columns "
            "WHERE table_name = 'agg_feed_health' AND column_name = ANY(%s)",
            (sorted(_COLUMNS),),
        )
        rows = cur.fetchall()
    assert all(nullable == "YES" for _, nullable in rows), rows
    return {name for name, _ in rows}


def test_rolling_back_drops_the_columns_and_migrating_up_restores_them(apply_schema):
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    try:
        assert _source_columns(conn) == _COLUMNS
        # Forced: the rollback also spans every later migration, and one is `-- DESTRUCTIVE`.
        migrate.migrate_down("0087", conn, force_destructive=True)
        assert _source_columns(conn) == set()

        migrate.migrate_up(conn)
        assert _source_columns(conn) == _COLUMNS
    finally:
        conn.rollback()
        conn.close()
