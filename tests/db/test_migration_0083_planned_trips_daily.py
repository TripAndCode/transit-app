"""planned_trips_daily keeps one planned-trip count per agency and service day,
the first one written, and goes with its agency."""

import os
from datetime import date

import psycopg2
import pytest

from db import migrate

_INSERT = (
    "INSERT INTO planned_trips_daily (agency_id, service_date, planned_trips) VALUES ($1, $2, $3) "
    "ON CONFLICT (agency_id, service_date) DO NOTHING"
)


def test_the_down_is_marked_destructive():
    down = migrate._MIGRATIONS_DIR / "0083_planned_trips_daily.down.sql"
    assert migrate.is_destructive_down(down.read_text())


@pytest.mark.asyncio
async def test_a_day_keeps_its_first_count_and_goes_with_its_agency(aconn):
    aid = await aconn.fetchval(
        "INSERT INTO agencies (agency_name, feed_url) VALUES ('a', 'http://a') RETURNING agency_id"
    )
    day = date(2026, 10, 27)
    await aconn.execute(_INSERT, aid, day, 120)
    await aconn.execute(_INSERT, aid, day, 95)
    row = await aconn.fetchrow(
        "SELECT planned_trips, recorded_at IS NOT NULL AS stamped FROM planned_trips_daily "
        "WHERE agency_id = $1 AND service_date = $2",
        aid,
        day,
    )
    assert (row["planned_trips"], row["stamped"]) == (120, True)

    await aconn.execute("DELETE FROM agencies WHERE agency_id = $1", aid)
    assert await aconn.fetchval("SELECT count(*) FROM planned_trips_daily") == 0


def _table_exists(conn) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.planned_trips_daily') IS NOT NULL")
        return cur.fetchone()[0]


def test_rolling_back_drops_the_table_and_migrating_up_restores_it(apply_schema):
    """`force_destructive=True` because the rollback drops saved plans, and this
    database is thrown away between runs."""
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    try:
        migrate.migrate_down("0082", conn, force_destructive=True)
        assert not _table_exists(conn)

        migrate.migrate_up(conn)
        assert _table_exists(conn)
    finally:
        conn.rollback()
        migrate.migrate_up(conn)
        conn.close()
