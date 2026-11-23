"""prediction_validations holds what a validation measured from archives a
mirror need not keep, so its down is destructive; its checks refuse counts
no validation could have produced."""

import os

import psycopg2
import pytest

from db import migrate

_VERSION = "0113"
_DOWN = migrate._MIGRATIONS_DIR / f"{_VERSION}_prediction_validations.down.sql"


def test_rolling_back_loses_the_records_so_the_down_is_marked():
    assert migrate.data_losing_tables(_DOWN.read_text()) == ["prediction_validations"]
    assert migrate.is_destructive_down(_DOWN.read_text())


def _exists(conn) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('prediction_validations') IS NOT NULL")
        return cur.fetchone()[0]


def test_rolling_back_drops_the_table_and_migrating_up_restores_it(apply_schema):
    below = max(version for version in migrate._versions_on_disk() if version < _VERSION)
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    try:
        assert _exists(conn)
        migrate.migrate_down(below, conn, force_destructive=True)
        assert not _exists(conn)
        migrate.migrate_up(conn)
        assert _exists(conn)
    finally:
        conn.rollback()
        conn.close()


_INSERT = (
    "INSERT INTO prediction_validations (agency_id, from_date, to_date, report_schema, early_tolerance_sec, "
    "late_tolerance_sec, stop_events, compared, kept_on_time, observed_on_time) "
    "VALUES (%s, %s, %s, 2, 60, 300, %s, %s, %s, %s)"
)


@pytest.mark.parametrize(
    ("dates", "counts"),
    [
        (("2026-11-21", "2026-11-13"), (1000, 850, 670, 620)),
        (("2026-11-13", "2026-11-21"), (1000, 0, 0, 0)),
        (("2026-11-13", "2026-11-21"), (1000, 1001, 670, 620)),
        (("2026-11-13", "2026-11-21"), (1000, 850, 851, 620)),
        (("2026-11-13", "2026-11-21"), (1000, 850, 670, -1)),
    ],
)
def test_the_checks_refuse_counts_no_validation_produces(pg_conn, agency_id, dates, counts):
    with pg_conn.cursor() as cur, pytest.raises(psycopg2.errors.CheckViolation):
        cur.execute(_INSERT, (agency_id, *dates, *counts))
