"""agg_route_headway_daily counts each day's bunched gaps and the gaps judged
for them. A row the migration finds, or one analyze cannot rebuild, reads
NULL for both: unknown, never zero bunching."""

import os

import psycopg2

from db import migrate

_VERSION = "0112"
_COLUMNS = {("agg_route_headway_daily", "bunched_count"), ("agg_route_headway_daily", "bunching_judged")}


def _columns(conn) -> set[tuple[str, str]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT table_name, column_name FROM information_schema.columns WHERE table_name = 'agg_route_headway_daily'"
        )
        return set(cur.fetchall()) & _COLUMNS


def test_a_day_built_before_the_columns_existed_is_not_measured(pg_conn, agency_id):
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agg_route_headway_daily (agency_id, route_code, date, actual_headway_median_sec, "
            "actual_samples) VALUES (%s, 'R1', '2026-10-01', 600, 2) RETURNING bunched_count, bunching_judged",
            (agency_id,),
        )
        assert cur.fetchone() == (None, None)


def test_the_down_drops_only_derived_columns_so_it_is_not_marked_destructive():
    down = migrate._MIGRATIONS_DIR / f"{_VERSION}_headway_bunching.down.sql"
    assert not migrate.is_destructive_down(down.read_text())
    assert migrate.data_losing_tables(down.read_text()) == []


def test_under_a_retention_floor_the_up_is_flagged_as_losing_data():
    """analyze never rebuilds a date before the floor, so the days there keep
    NULL counts for good: the operator names the migration to apply it."""
    up = migrate._MIGRATIONS_DIR / f"{_VERSION}_headway_bunching.up.sql"
    assert set(migrate.data_losing_tables(up.read_text(), retention_floor=True)) == {"agg_route_headway_daily"}
    assert migrate.data_losing_tables(up.read_text()) == []


def test_rolling_back_drops_the_columns_and_migrating_up_restores_them(apply_schema):
    below = max(version for version in migrate._versions_on_disk() if version < _VERSION)
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    try:
        assert _columns(conn) == _COLUMNS
        migrate.migrate_down(below, conn)
        assert _columns(conn) == set()

        migrate.migrate_up(conn)
        assert _columns(conn) == _COLUMNS
    finally:
        conn.rollback()
        conn.close()
