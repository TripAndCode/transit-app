"""The admin user search matches `normalize(email, NFKC)` and
`normalize(name, NFKC)`, and the planner uses an expression index only for a
predicate on that same expression, so the trigram indexes cover the folded
columns and not the raw ones."""

import os

import psycopg2
import pytest

from api.routers.admin import _user_search_predicate
from db.migrate import migrate_down, migrate_up

_FOLDED = {"idx_users_email_nfkc_trgm": "email", "idx_users_name_nfkc_trgm": "name"}
_RAW = {"idx_users_email_trgm", "idx_users_name_trgm"}


def _user_indexes(conn) -> set[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT indexname FROM pg_indexes WHERE tablename = 'users'")
        return {r[0] for r in cur.fetchall()}


@pytest.mark.asyncio
@pytest.mark.parametrize(("indexname", "column"), sorted(_FOLDED.items()))
async def test_users_has_a_trigram_index_on_the_folded_column(aconn, indexname, column):
    indexdef = await aconn.fetchval(
        "SELECT indexdef FROM pg_indexes WHERE tablename = 'users' AND indexname = $1", indexname
    )
    assert indexdef is not None
    assert indexdef.endswith(f"USING gin (NORMALIZE({column}, NFKC) gin_trgm_ops)")


@pytest.mark.asyncio
@pytest.mark.parametrize("indexname", sorted(_RAW))
async def test_the_raw_column_trigram_index_is_gone(aconn, indexname):
    assert await aconn.fetchval("SELECT 1 FROM pg_indexes WHERE indexname = $1", indexname) is None


@pytest.mark.asyncio
async def test_the_admin_search_predicate_can_use_both_indexes(aconn):
    """`enable_seqscan = off` makes the planner take any index it can use, so a
    plan without these two means the query's expression and the index's differ."""
    async with aconn.transaction():
        await aconn.execute("SET LOCAL enable_seqscan = off")
        rows = await aconn.fetch(f"EXPLAIN SELECT count(*) FROM users WHERE {_user_search_predicate(1)}", "%tanaka%")
    plan = "\n".join(r[0] for r in rows)
    for indexname in _FOLDED:
        assert indexname in plan, plan


def test_rolling_back_restores_the_raw_indexes_and_migrating_up_replaces_them(apply_schema):
    """`force_destructive=True` because the rollback spans every migration
    above 0078, and this database is thrown away between runs."""
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    try:
        migrate_down("0078", conn, force_destructive=True)
        indexes = _user_indexes(conn)
        assert _RAW <= indexes
        assert not indexes & _FOLDED.keys()

        migrate_up(conn)
        indexes = _user_indexes(conn)
        assert _FOLDED.keys() <= indexes
        assert not indexes & _RAW
    finally:
        conn.rollback()
        migrate_up(conn)
        conn.close()
