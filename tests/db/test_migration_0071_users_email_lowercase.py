"""users.email is stored lowercased, so two accounts cannot differ only in
case; the migration lowercases what is there and refuses to pick between two
accounts that already do."""

import asyncpg
import psycopg2.errors
import pytest

from db import migrate


@pytest.mark.asyncio
async def test_a_mixed_case_address_cannot_be_stored(aconn):
    with pytest.raises(asyncpg.CheckViolationError):
        await aconn.execute("INSERT INTO users (email) VALUES ('Mixed@Case')")


def test_the_down_is_not_marked_destructive():
    down = migrate._MIGRATIONS_DIR / "0071_users_email_lowercase.down.sql"
    assert not migrate.is_destructive_down(down.read_text())


def _emails(pg_conn, like):
    with pg_conn.cursor() as cur:
        cur.execute("SELECT email FROM users WHERE lower(email) LIKE %s", (like,))
        return [r[0] for r in cur.fetchall()]


def _delete(pg_conn, like):
    with pg_conn.cursor() as cur:
        cur.execute("DELETE FROM users WHERE lower(email) LIKE %s", (like,))
    pg_conn.commit()


def test_up_lowercases_the_stored_addresses(pg_conn):
    """Rolling back to 0070 also rolls back every later migration; this test is
    about 0071's round trip alone."""
    try:
        migrate.migrate_down("0070", pg_conn, force_destructive=True)
        with pg_conn.cursor() as cur:
            cur.execute("INSERT INTO users (email) VALUES ('Up.Case@X')")
        pg_conn.commit()
        migrate.migrate_up(pg_conn)
        assert _emails(pg_conn, "up.case@x") == ["up.case@x"]
    finally:
        pg_conn.rollback()
        migrate.migrate_up(pg_conn)
        _delete(pg_conn, "up.case@x")


def test_up_refuses_two_accounts_that_differ_only_in_case(pg_conn):
    try:
        migrate.migrate_down("0070", pg_conn, force_destructive=True)
        with pg_conn.cursor() as cur:
            cur.execute("INSERT INTO users (email) VALUES ('twin@x'), ('Twin@X')")
        pg_conn.commit()
        with pytest.raises(psycopg2.errors.RaiseException, match="differ only in case"):
            migrate.migrate_up(pg_conn)
        pg_conn.rollback()
        assert set(_emails(pg_conn, "twin@x")) == {"Twin@X", "twin@x"}
    finally:
        pg_conn.rollback()
        _delete(pg_conn, "twin@x")
        migrate.migrate_up(pg_conn)
