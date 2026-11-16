"""feed_quality_monthly states each month's poll success and outage minutes.
A month built before the columns existed keeps none of them until analyze
rebuilds it, never a 0 that would pass for measured; a month built from now
on states each figure exactly when its reason is empty. Rolling back loses
them, so the down is marked destructive."""

import os

import psycopg2
import pytest

from db import migrate
from pipeline import poll_outages

_VERSION = "0107"


def _columns(conn) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'feed_quality_monthly' AND column_name = ANY(%s)",
            (list(poll_outages.COLUMNS),),
        )
        return {row[0] for row in cur.fetchall()}


_MEASURED = {
    "poll_days": 30,
    "in_service_min": 28800.0,
    "poll_interval_sec": 30.0,
    "polls_in_service": 57000,
    "polls_expected": 57600,
    "poll_success_pct": 99.0,
    "feed_outage_min": 12.5,
    "feed_outage_longest_min": 8.0,
    "collector_outage_min": 0.0,
    "collector_outage_longest_min": 0.0,
    "polls_missing": None,
    "outage_missing": None,
    "peer_rows": 1000,
}


def _insert(conn, agency_id: int, month: str, polls: dict | None) -> None:
    """A month with the poll columns *polls* when given."""
    names, values = "", ()
    if polls is not None:
        names = "".join(f", {column}" for column in polls)
        values = tuple(polls.values())
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO feed_quality_monthly (agency_id, month, through_date, covered_days, window_days, "
            "stop_plan_days, message_age_polls, raw_samples, clamp_count, stop_time_updates, "
            "rt_completeness_missing, stop_completeness_missing, message_age_missing, clamp_missing, "
            f"field_coverage_missing, vehicle_positions_missing{names}) VALUES (%s, %s, %s::date + 27, 28, 28, 0, "
            "0, 0, 0, 0, 'no_plan', 'no_plan', 'no_data', 'no_data', 'no_data', 'not_recorded'"
            f"{', %s' * len(values)})",
            (agency_id, month, month, *values),
        )


def test_the_down_is_marked_destructive():
    down = migrate._MIGRATIONS_DIR / f"{_VERSION}_feed_quality_poll_outages.down.sql"
    assert migrate.is_destructive_down(down.read_text())


def test_a_measured_month_and_one_with_its_reasons_are_both_accepted(pg_conn, agency_id):
    _insert(pg_conn, agency_id, "2026-10-01", _MEASURED)
    unknown = {
        **dict.fromkeys(_MEASURED),
        "poll_days": 0,
        "polls_missing": "no_service_hours",
        "outage_missing": "no_service_hours",
        "peer_rows": 0,
    }
    _insert(pg_conn, agency_id, "2026-09-01", unknown)
    split_unknown = {**_MEASURED, **dict.fromkeys(poll_outages.FIGURES[5:]), "outage_missing": "no_peer"}
    _insert(pg_conn, agency_id, "2026-08-01", split_unknown)


@pytest.mark.parametrize(
    "polls",
    [
        # A figure with its reason, or neither.
        {**_MEASURED, "polls_missing": "no_service_hours", "outage_missing": "no_service_hours"},
        {**_MEASURED, "poll_success_pct": None},
        # Poll figures missing for one reason but the outage for another.
        {
            **_MEASURED,
            **dict.fromkeys(poll_outages.FIGURES),
            "polls_missing": "no_plan",
            "outage_missing": "no_peer",
        },
        # One outage figure without the others.
        {**_MEASURED, "collector_outage_min": None},
        {**_MEASURED, "poll_success_pct": 100.5},
        {**_MEASURED, "peer_rows": None},
    ],
)
def test_a_month_states_each_figure_exactly_when_its_reason_is_empty(pg_conn, agency_id, polls):
    with pytest.raises(psycopg2.errors.CheckViolation):
        _insert(pg_conn, agency_id, "2026-10-01", polls)


def test_a_month_built_before_the_columns_existed_keeps_none_and_rolling_back_drops_them(apply_schema):
    below = max(version for version in migrate._versions_on_disk() if version < _VERSION)
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    agency_id = None
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO agencies (agency_name, feed_url) VALUES ('0107', 'http://0107.example.com') "
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
        assert _columns(conn) == set(poll_outages.COLUMNS)
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {', '.join(poll_outages.COLUMNS)} FROM feed_quality_monthly WHERE agency_id = %s",
                (agency_id,),
            )
            assert cur.fetchall() == [(None,) * len(poll_outages.COLUMNS)]
    finally:
        conn.rollback()
        if agency_id is not None:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM feed_quality_monthly WHERE agency_id = %s", (agency_id,))
                cur.execute("DELETE FROM agencies WHERE agency_id = %s", (agency_id,))
            conn.commit()
        conn.close()
