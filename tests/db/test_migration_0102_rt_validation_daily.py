"""rt_validation_daily holds one validator summary per agency and day, and
feed_quality_monthly states its validator figures beside a reason when it has
none. A month built before the columns existed keeps none of them, never a 0
that would read as no findings. Rolling back loses the ingested summaries, so
the down is marked destructive."""

import os

import psycopg2
import pytest

from db import migrate

_VERSION = "0102"
_COLUMNS = (
    "validator_ok_days",
    "validator_failed_days",
    "validator_files_validated",
    "validator_rules",
    "validator_missing",
    "validator_ingested_at",
)


def _columns(conn) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'feed_quality_monthly' AND column_name = ANY(%s)",
            (list(_COLUMNS),),
        )
        return {row[0] for row in cur.fetchall()}


def _insert(conn, agency_id: int, month: str, validator: dict | None = None) -> None:
    validator = validator or {}
    names = "".join(f", {name}" for name in validator)
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO feed_quality_monthly (agency_id, month, through_date, covered_days, window_days, "
            "stop_plan_days, message_age_polls, raw_samples, clamp_count, stop_time_updates, "
            "rt_completeness_missing, stop_completeness_missing, message_age_missing, clamp_missing, "
            f"field_coverage_missing, vehicle_positions_missing{names}) VALUES (%s, %s, %s::date + 27, 28, 28, 0, 0, "
            "0, 0, 0, 'no_plan', 'no_plan', 'no_data', 'no_data', 'no_data', 'not_recorded'"
            + ", %s" * len(validator)
            + ")",
            (agency_id, month, month, *validator.values()),
        )


def _summary(conn, agency_id: int, status: str, reason: str | None, given: int = 60, validated: int = 58) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO rt_validation_daily (agency_id, service_date, status, reason, files_given, files_validated, "
            "summary, etag) VALUES (%s, '2026-11-10', %s, %s, %s, %s, '{}', 'e')",
            (agency_id, status, reason, given, validated),
        )


def test_the_down_is_marked_destructive():
    down = migrate._MIGRATIONS_DIR / f"{_VERSION}_rt_validation_daily.down.sql"
    assert migrate.is_destructive_down(down.read_text())


def test_a_summary_states_a_reason_exactly_when_its_validator_did_not_run(pg_conn, agency_id):
    _summary(pg_conn, agency_id, "ok", None)
    pg_conn.rollback()
    _summary(pg_conn, agency_id, "no_archive", "no archive", 0, 0)
    pg_conn.rollback()
    with pytest.raises(psycopg2.errors.CheckViolation):
        _summary(pg_conn, agency_id, "ok", "stray")
    pg_conn.rollback()
    with pytest.raises(psycopg2.errors.CheckViolation):
        _summary(pg_conn, agency_id, "graded", "x")
    pg_conn.rollback()
    with pytest.raises(psycopg2.errors.CheckViolation):
        _summary(pg_conn, agency_id, "ok", None, 10, 11)


_VALIDATED = {
    "validator_ok_days": 1,
    "validator_failed_days": 0,
    "validator_files_validated": 58,
    "validator_rules": "{}",
    "validator_missing": None,
    "validator_ingested_at": "2026-11-11T01:00:00Z",
}


@pytest.mark.parametrize(
    "validator",
    [
        pytest.param({}, id="built-before-the-columns"),
        pytest.param(_VALIDATED, id="validated"),
        pytest.param(
            {
                **_VALIDATED,
                "validator_ok_days": 0,
                "validator_files_validated": None,
                "validator_rules": None,
                "validator_missing": "not_validated",
                "validator_ingested_at": None,
            },
            id="no-summary",
        ),
    ],
)
def test_a_month_states_its_validator_figures_consistently(pg_conn, agency_id, validator):
    _insert(pg_conn, agency_id, "2026-10-01", validator)


@pytest.mark.parametrize(
    "over",
    [
        pytest.param({"validator_rules": None}, id="validated-without-rules"),
        pytest.param({"validator_missing": "not_validated"}, id="validated-with-a-reason"),
        pytest.param({"validator_ok_days": 0}, id="figures-without-an-ok-day"),
        pytest.param({"validator_files_validated": None}, id="rules-without-polls"),
        pytest.param({"validator_ingested_at": None}, id="summaries-without-a-stamp"),
        pytest.param({"validator_failed_days": None}, id="half-built"),
    ],
)
def test_a_month_cannot_state_inconsistent_validator_figures(pg_conn, agency_id, over):
    with pytest.raises(psycopg2.errors.CheckViolation):
        _insert(pg_conn, agency_id, "2026-10-01", {**_VALIDATED, **over})


def test_a_month_built_before_the_columns_existed_keeps_none_and_rolling_back_drops_them(apply_schema):
    below = max(version for version in migrate._versions_on_disk() if version < _VERSION)
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    agency_id = None
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO agencies (agency_name, feed_url) VALUES ('0102', 'http://0102.example.com') "
                "RETURNING agency_id"
            )
            agency_id = cur.fetchone()[0]
        conn.commit()

        migrate.migrate_down(below, conn, force_destructive=True)
        assert _columns(conn) == set()
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('rt_validation_daily')")
            assert cur.fetchone()[0] is None
        _insert(conn, agency_id, "2026-10-01")
        conn.commit()

        migrate.migrate_up(conn)
        assert _columns(conn) == set(_COLUMNS)
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {', '.join(_COLUMNS)} FROM feed_quality_monthly WHERE agency_id = %s",
                (agency_id,),
            )
            assert cur.fetchall() == [(None,) * len(_COLUMNS)]
    finally:
        conn.rollback()
        if agency_id is not None:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM feed_quality_monthly WHERE agency_id = %s", (agency_id,))
                cur.execute("DELETE FROM agencies WHERE agency_id = %s", (agency_id,))
            conn.commit()
        conn.close()
