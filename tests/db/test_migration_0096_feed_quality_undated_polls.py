"""feed_quality_monthly counts each month's archived polls whose file name
gives no capture instant, beside the message age they are left out of.
Rolling back loses that count until analyze rebuilds the months, so the down
is marked destructive."""

import os

import psycopg2

from db import migrate

_VERSION = "0096"


def _has_column(conn) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'feed_quality_monthly' AND column_name = 'message_age_undated_polls')"
        )
        return cur.fetchone()[0]


def test_the_down_is_marked_destructive():
    down = migrate._MIGRATIONS_DIR / f"{_VERSION}_feed_quality_undated_polls.down.sql"
    assert migrate.is_destructive_down(down.read_text())


def test_a_month_built_before_the_count_existed_reads_none_left_out(pg_conn, agency_id):
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO feed_quality_monthly (agency_id, month, through_date, covered_days, window_days, "
            "stop_plan_days, message_age_polls, raw_samples, clamp_count, stop_time_updates, "
            "rt_completeness_missing, stop_completeness_missing, message_age_missing, clamp_missing, "
            "field_coverage_missing, vehicle_positions_missing) VALUES (%s, '2026-10-01', '2026-10-31', 31, 31, "
            "0, 0, 0, 0, 0, 'no_plan', 'no_plan', 'no_data', 'no_data', 'no_data', 'not_recorded') "
            "RETURNING message_age_undated_polls",
            (agency_id,),
        )
        assert cur.fetchone()[0] == 0


def test_rolling_back_drops_the_count_and_migrating_up_restores_it(apply_schema):
    below = max(version for version in migrate._versions_on_disk() if version < _VERSION)
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    try:
        assert _has_column(conn)
        migrate.migrate_down(below, conn, force_destructive=True)
        assert not _has_column(conn)

        migrate.migrate_up(conn)
        assert _has_column(conn)
    finally:
        conn.rollback()
        conn.close()
