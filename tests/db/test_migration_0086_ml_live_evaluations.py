"""ml_live_evaluations holds one live scorecard per weekly run, keyed by run date."""

import json
import os
from datetime import date

import psycopg2
import pytest

from db import migrate


def test_the_down_is_marked_destructive():
    down = migrate._MIGRATIONS_DIR / "0086_ml_live_evaluations.down.sql"
    assert migrate.is_destructive_down(down.read_text())


@pytest.mark.asyncio
async def test_the_table_keeps_one_scorecard_per_run_date(aconn):
    insert = (
        "INSERT INTO ml_live_evaluations (run_date, schema_version, document) VALUES ($1, 1, $2::jsonb) "
        "ON CONFLICT (run_date) DO UPDATE SET document = EXCLUDED.document, ingested_at = now()"
    )
    week = date(2026, 11, 8)
    await aconn.execute(insert, week, json.dumps({"skill": {"live": 0.01}}))
    await aconn.execute(insert, week, json.dumps({"skill": {"live": 0.02}}))
    row = await aconn.fetchrow("SELECT document, ingested_at IS NOT NULL AS stamped FROM ml_live_evaluations")
    assert (json.loads(row["document"]), row["stamped"]) == ({"skill": {"live": 0.02}}, True)
    assert await aconn.fetchval("SELECT count(*) FROM ml_live_evaluations") == 1


def _table_exists(conn) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.ml_live_evaluations') IS NOT NULL")
        return cur.fetchone()[0]


def test_rolling_back_drops_the_table_and_migrating_up_restores_it(apply_schema):
    """`force_destructive=True` because the rollback drops ingested scorecards, and this
    database is thrown away between runs."""
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    try:
        migrate.migrate_down("0085", conn, force_destructive=True)
        assert not _table_exists(conn)

        migrate.migrate_up(conn)
        assert _table_exists(conn)
    finally:
        conn.rollback()
