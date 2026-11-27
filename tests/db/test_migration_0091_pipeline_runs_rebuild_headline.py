"""pipeline_runs keeps a rebuild's headline before and after on its analyze run,
which nothing can rebuild once the column is rolled back."""

import os

import psycopg2

from db import migrate


def test_the_down_is_marked_destructive():
    down = migrate._MIGRATIONS_DIR / "0091_pipeline_runs_rebuild_headline.down.sql"
    assert migrate.is_destructive_down(down.read_text())


def _headline_column(conn) -> tuple[str, str] | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT data_type, is_nullable FROM information_schema.columns "
            "WHERE table_name = 'pipeline_runs' AND column_name = 'rebuild_headline'"
        )
        return cur.fetchone()


def test_rolling_back_drops_the_column_and_migrating_up_restores_it(apply_schema):
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    try:
        assert _headline_column(conn) == ("jsonb", "YES")
        migrate.migrate_down("0090", conn, force_destructive=True)
        assert _headline_column(conn) is None

        migrate.migrate_up(conn)
        assert _headline_column(conn) == ("jsonb", "YES")
    finally:
        conn.close()
