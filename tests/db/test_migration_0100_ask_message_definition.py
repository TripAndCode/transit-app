"""ask_conversation_messages.definition holds the method stamp a saved Ask card
was made under. It is nullable, NULL being a message saved before the column
or one with no stamp, and rolling it back loses the stamps, which nothing can
rebuild."""

import os

import psycopg2

from db import migrate


def test_the_down_is_marked_destructive():
    down = migrate._MIGRATIONS_DIR / "0100_ask_message_definition.down.sql"
    assert migrate.is_destructive_down(down.read_text())


def _definition_column(conn) -> list[tuple[str, str, str]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
            "WHERE table_name = 'ask_conversation_messages' AND column_name = 'definition'"
        )
        return cur.fetchall()


def test_rolling_back_drops_the_column_and_migrating_up_restores_it(apply_schema):
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    try:
        assert _definition_column(conn) == [("definition", "jsonb", "YES")]
        migrate.migrate_down("0099", conn, force_destructive=True)
        assert _definition_column(conn) == []

        migrate.migrate_up(conn)
        assert _definition_column(conn) == [("definition", "jsonb", "YES")]
    finally:
        conn.rollback()
        conn.close()
