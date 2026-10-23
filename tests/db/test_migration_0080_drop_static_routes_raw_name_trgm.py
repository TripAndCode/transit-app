"""Every route-name match compares `normalize(col, NFKC)`, and the planner uses
an index only for a predicate on the indexed expression, so the trigram
indexes on the raw route-name columns serve no query and are dropped."""

import os

import psycopg2
import pytest

from db.migrate import migrate_down, migrate_up

_RAW = {
    "idx_static_routes_short_name_trgm": "route_short_name",
    "idx_static_routes_long_name_trgm": "route_long_name",
}


def _static_routes_indexes(conn) -> dict[str, str]:
    with conn.cursor() as cur:
        cur.execute("SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'static_routes'")
        return dict(cur.fetchall())


@pytest.mark.asyncio
@pytest.mark.parametrize("indexname", sorted(_RAW))
async def test_the_raw_route_name_trigram_index_is_gone(aconn, indexname):
    assert await aconn.fetchval("SELECT 1 FROM pg_indexes WHERE indexname = $1", indexname) is None


def test_rolling_back_restores_the_raw_indexes_and_migrating_up_drops_them(apply_schema):
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    try:
        migrate_down("0079", conn)
        indexes = _static_routes_indexes(conn)
        for indexname, column in _RAW.items():
            assert indexes.get(indexname, "").endswith(f"USING gin ({column} gin_trgm_ops)"), indexes

        migrate_up(conn)
        assert not _static_routes_indexes(conn).keys() & _RAW.keys()
    finally:
        conn.rollback()
        migrate_up(conn)
        conn.close()
