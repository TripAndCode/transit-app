"""agg_daily_trend.date is a DATE: parameters arrive typed, the purge compares
date to date, and the migration round-trips through its down without loss."""

from datetime import date

import asyncpg
import pytest

from db import migrate


def _column_type(pg_conn, table, column):
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT data_type FROM information_schema.columns WHERE table_name = %s AND column_name = %s",
            (table, column),
        )
        return cur.fetchone()[0]


def test_agg_daily_trend_date_is_a_date_column(pg_conn):
    assert _column_type(pg_conn, "agg_daily_trend", "date") == "date"


def test_no_incremental_table_stores_its_date_as_text(pg_conn):
    from pipeline.analyze import _INCREMENTAL_AGG_TABLES

    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.columns WHERE column_name = 'date' "
            "AND data_type = 'text' AND table_name = ANY(%s)",
            (sorted(_INCREMENTAL_AGG_TABLES),),
        )
        assert cur.fetchall() == []


@pytest.mark.asyncio
async def test_date_column_rejects_text_parameter_and_accepts_date(aconn, aagency_id):
    """Every seeding helper binds a date object: asyncpg refuses ISO text for a DATE parameter."""
    sql = (
        "INSERT INTO agg_daily_trend (agency_id, date, route_code, service_type, avg_min, samples) "
        "VALUES ($1, $2, 'R', '平日', 1.0, 1)"
    )
    with pytest.raises(asyncpg.DataError):
        await aconn.execute(sql, aagency_id, "2026-05-18")
    day = date(2026, 5, 18)
    await aconn.execute(sql, aagency_id, day)
    assert await aconn.fetchval("SELECT date FROM agg_daily_trend WHERE agency_id = $1", aagency_id) == day


def test_the_lossless_down_is_not_marked_destructive():
    down = migrate._MIGRATIONS_DIR / "0068_agg_daily_trend_date_type.down.sql"
    assert not migrate.is_destructive_down(down.read_text())


def test_down_restores_iso_text_and_up_restores_date(pg_conn, agency_id):
    """Rolling back to 0067 also rolls back every later migration, some of which
    may be marked destructive; this test is about 0068's round trip alone."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agg_daily_trend (agency_id, date, route_code, service_type, avg_min, samples) "
            "VALUES (%s, %s, 'R', '平日', 1.0, 1)",
            (agency_id, date(2026, 5, 18)),
        )
    pg_conn.commit()
    try:
        migrate.migrate_down("0067", pg_conn, force_destructive=True)
        assert _column_type(pg_conn, "agg_daily_trend", "date") == "text"
        with pg_conn.cursor() as cur:
            cur.execute("SELECT date FROM agg_daily_trend WHERE agency_id = %s", (agency_id,))
            assert cur.fetchone()[0] == "2026-05-18"
        pg_conn.rollback()
    finally:
        # Restore the schema every later test in the session expects, even
        # when an assertion above failed partway through the round trip.
        migrate.migrate_up(pg_conn)
    assert _column_type(pg_conn, "agg_daily_trend", "date") == "date"
    with pg_conn.cursor() as cur:
        cur.execute("SELECT date FROM agg_daily_trend WHERE agency_id = %s", (agency_id,))
        assert cur.fetchone()[0] == date(2026, 5, 18)
