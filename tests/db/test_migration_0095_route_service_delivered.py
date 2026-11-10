"""agg_service_delivered_daily counts each day's trips not run per route, and
planned_trips_daily keeps each saved day's plan split by route. Rolling back
loses the saved splits, so the down is marked destructive, and keeps each
day's count of trips not run, its routes summed."""

import os
from datetime import date

import psycopg2
import pytest

from db import migrate

_VERSION = "0095"


def _state(conn) -> tuple[bool, bool]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'agg_service_delivered_daily' AND column_name = 'route_code'), "
            "EXISTS (SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'planned_trips_daily' AND column_name = 'planned_trips_by_route')"
        )
        return cur.fetchone()


def test_the_down_is_marked_destructive():
    down = migrate._MIGRATIONS_DIR / f"{_VERSION}_route_service_delivered.down.sql"
    assert migrate.is_destructive_down(down.read_text())


def test_a_day_holds_one_row_per_route(pg_conn, agency_id):
    with pg_conn.cursor() as cur:
        insert = (
            "INSERT INTO agg_service_delivered_daily (agency_id, date, route_code, non_executed_trips) "
            "VALUES (%s, '2026-04-01', %s, 1)"
        )
        cur.execute(insert, (agency_id, "R1"))
        cur.execute(insert, (agency_id, "R2"))
        cur.execute(insert, (agency_id, ""))
        with pytest.raises(psycopg2.errors.UniqueViolation):
            cur.execute(insert, (agency_id, "R1"))


def test_rolling_back_sums_each_days_routes_and_migrating_up_restores_both(apply_schema):
    below = max(version for version in migrate._versions_on_disk() if version < _VERSION)
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    agency_id = None
    try:
        assert _state(conn) == (True, True)
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO agencies (agency_name, feed_url) VALUES ('0095', 'http://0095.example.com') "
                "RETURNING agency_id"
            )
            agency_id = cur.fetchone()[0]
            cur.executemany(
                "INSERT INTO agg_service_delivered_daily (agency_id, date, route_code, non_executed_trips) "
                "VALUES (%s, %s, %s, %s)",
                [
                    (agency_id, date(2026, 4, 1), "R1", 2),
                    (agency_id, date(2026, 4, 1), "", 1),
                    (agency_id, date(2026, 4, 2), "R2", 4),
                ],
            )
        conn.commit()

        migrate.migrate_down(below, conn, force_destructive=True)
        assert _state(conn) == (False, False)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT date, non_executed_trips FROM agg_service_delivered_daily WHERE agency_id = %s ORDER BY date",
                (agency_id,),
            )
            assert cur.fetchall() == [(date(2026, 4, 1), 3), (date(2026, 4, 2), 4)]

        migrate.migrate_up(conn)
        assert _state(conn) == (True, True)
    finally:
        conn.rollback()
        if agency_id is not None:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM agg_service_delivered_daily WHERE agency_id = %s", (agency_id,))
                cur.execute("DELETE FROM agencies WHERE agency_id = %s", (agency_id,))
            conn.commit()
        conn.close()
