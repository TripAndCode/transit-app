"""route_headway_by_content keeps each timetable's scheduled headway sums
beside its median, and agg_route_headway_daily carries each day's own
timetable's figures. Rolling back loses the sums of every timetable no longer
loaded, so the down is marked destructive."""

import os

import psycopg2

from db import migrate

_VERSION = "0098"
_COLUMNS = {
    ("route_headway_by_content", "scheduled_headway_sum_sec"),
    ("route_headway_by_content", "scheduled_headway_sumsq_sec2"),
    ("agg_route_headway_daily", "scheduled_headway_median_sec"),
    ("agg_route_headway_daily", "is_high_frequency"),
    ("agg_route_headway_daily", "scheduled_headway_sum_sec"),
    ("agg_route_headway_daily", "scheduled_headway_sumsq_sec2"),
}


def _columns(conn) -> set[tuple[str, str]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_name IN ('route_headway_by_content', 'agg_route_headway_daily')"
        )
        return set(cur.fetchall()) & _COLUMNS


def test_the_down_is_marked_destructive():
    down = migrate._MIGRATIONS_DIR / f"{_VERSION}_headway_quality_in_force.down.sql"
    assert migrate.is_destructive_down(down.read_text())


def test_a_day_built_before_the_columns_existed_is_not_judged(pg_conn, agency_id):
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agg_route_headway_daily (agency_id, route_code, date, actual_headway_median_sec, "
            "actual_samples) VALUES (%s, 'R1', '2026-10-01', 600, 2) RETURNING is_high_frequency",
            (agency_id,),
        )
        assert cur.fetchone()[0] is None


def test_rolling_back_drops_the_columns_and_migrating_up_restores_them(apply_schema):
    below = max(version for version in migrate._versions_on_disk() if version < _VERSION)
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    try:
        assert _columns(conn) == _COLUMNS
        migrate.migrate_down(below, conn, force_destructive=True)
        assert _columns(conn) == set()

        migrate.migrate_up(conn)
        assert _columns(conn) == _COLUMNS
    finally:
        conn.rollback()
        conn.close()
