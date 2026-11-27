"""feed_quality_monthly holds one scorecard row per agency and month, each
figure beside its reason, and planned_trips_daily keeps each saved day's stop
events. Rolling back loses those, so the down is marked destructive."""

import os
from datetime import date

import psycopg2
import pytest

from db import migrate

_VERSION = "0093"


def _state(conn) -> tuple[bool, bool]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT to_regclass('feed_quality_monthly') IS NOT NULL, EXISTS (SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'planned_trips_daily' AND column_name = 'planned_stop_events')"
        )
        return cur.fetchone()


def test_the_down_is_marked_destructive():
    down = migrate._MIGRATIONS_DIR / f"{_VERSION}_feed_quality_monthly.down.sql"
    assert migrate.is_destructive_down(down.read_text())


_INSERT = (
    "INSERT INTO feed_quality_monthly (agency_id, month, through_date, covered_days, window_days, stop_plan_days, "
    "message_age_polls, raw_samples, clamp_count, stop_time_updates, rt_completeness_pct, rt_completeness_missing, "
    "stop_completeness_missing, message_age_missing, clamp_missing, field_coverage_missing, vehicle_positions_missing) "
    "VALUES (%s, %s, %s, 30, 30, 0, 0, 0, 0, 0, %s, %s, 'no_plan', 'no_data', 'no_data', 'no_data', 'not_recorded')"
)


def test_a_figure_stands_beside_its_reason_never_with_it(pg_conn, agency_id):
    with pg_conn.cursor() as cur:
        cur.execute(_INSERT, (agency_id, date(2026, 10, 1), date(2026, 10, 30), None, "no_plan"))
        for pct, missing in ((None, None), (50.0, "no_plan")):
            cur.execute("SAVEPOINT s")
            with pytest.raises(psycopg2.errors.CheckViolation):
                cur.execute(_INSERT, (agency_id, date(2026, 9, 1), date(2026, 9, 30), pct, missing))
            cur.execute("ROLLBACK TO SAVEPOINT s")


@pytest.mark.parametrize(
    ("month", "through"), [(date(2026, 10, 2), date(2026, 10, 30)), (date(2026, 10, 1), date(2026, 11, 1))]
)
def test_a_row_is_one_month_counted_within_it(pg_conn, agency_id, month, through):
    with pg_conn.cursor() as cur, pytest.raises(psycopg2.errors.CheckViolation):
        cur.execute(_INSERT, (agency_id, month, through, None, "no_plan"))


def test_rolling_back_drops_both_and_migrating_up_restores_them(apply_schema):
    below = max(version for version in migrate._versions_on_disk() if version < _VERSION)
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    try:
        assert _state(conn) == (True, True)
        migrate.migrate_down(below, conn, force_destructive=True)
        assert _state(conn) == (False, False)

        migrate.migrate_up(conn)
        assert _state(conn) == (True, True)
    finally:
        conn.rollback()
        conn.close()
