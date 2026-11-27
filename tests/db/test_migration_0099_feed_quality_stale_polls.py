"""feed_quality_monthly counts each month's polls whose message is older than
65 s at capture, and their share of the polls measured. A month built before
the columns existed keeps no share until analyze rebuilds it, never a 0 that
would pass for measured; a month built from now on states the share exactly
when it states a message age. Rolling back loses the count, so the down is
marked destructive."""

import os

import psycopg2
import pytest

from db import migrate

_VERSION = "0099"
_COLUMNS = ("message_age_stale_polls", "message_age_stale_pct")


def _columns(conn) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'feed_quality_monthly' AND column_name = ANY(%s)",
            (list(_COLUMNS),),
        )
        return {row[0] for row in cur.fetchall()}


def _insert(conn, agency_id: int, month: str, stale: tuple | None) -> None:
    """A month with a message age, and the stale count and share *stale* when given."""
    names, values = "", ""
    if stale is not None:
        names, values = ", message_age_stale_polls, message_age_stale_pct", f", {stale[0]}, {stale[1]}"
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO feed_quality_monthly (agency_id, month, through_date, covered_days, window_days, "
            "stop_plan_days, message_age_polls, message_age_p50_sec, message_age_p95_sec, raw_samples, clamp_count, "
            "stop_time_updates, rt_completeness_missing, stop_completeness_missing, clamp_missing, "
            f"field_coverage_missing, vehicle_positions_missing{names}) VALUES (%s, %s, %s::date + 27, 28, 28, 0, 4, "
            f"20, 36, 0, 0, 0, 'no_plan', 'no_plan', 'no_data', 'no_data', 'not_recorded'{values})",
            (agency_id, month, month),
        )


def test_the_down_is_marked_destructive():
    down = migrate._MIGRATIONS_DIR / f"{_VERSION}_feed_quality_stale_polls.down.sql"
    assert migrate.is_destructive_down(down.read_text())


def test_a_month_states_its_stale_share_exactly_when_it_states_a_message_age(pg_conn, agency_id):
    _insert(pg_conn, agency_id, "2026-10-01", (1, 25.0))
    with pytest.raises(psycopg2.errors.CheckViolation):
        _insert(pg_conn, agency_id, "2026-09-01", None)


def test_a_month_built_before_the_share_existed_keeps_none_and_rolling_back_drops_it(apply_schema):
    below = max(version for version in migrate._versions_on_disk() if version < _VERSION)
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    agency_id = None
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO agencies (agency_name, feed_url) VALUES ('0099', 'http://0099.example.com') "
                "RETURNING agency_id"
            )
            agency_id = cur.fetchone()[0]
        conn.commit()

        # Forced: the rollback is `-- DESTRUCTIVE`.
        migrate.migrate_down(below, conn, force_destructive=True)
        assert _columns(conn) == set()
        _insert(conn, agency_id, "2026-10-01", None)
        conn.commit()

        migrate.migrate_up(conn)
        assert _columns(conn) == set(_COLUMNS)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT message_age_stale_polls, message_age_stale_pct FROM feed_quality_monthly WHERE agency_id = %s",
                (agency_id,),
            )
            assert cur.fetchall() == [(None, None)]
    finally:
        conn.rollback()
        if agency_id is not None:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM feed_quality_monthly WHERE agency_id = %s", (agency_id,))
                cur.execute("DELETE FROM agencies WHERE agency_id = %s", (agency_id,))
            conn.commit()
        conn.close()
