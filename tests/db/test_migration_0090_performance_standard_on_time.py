"""A route's performance standard can set an on-time target, a share between 0
and 100, beside the excess-wait and vehicle-km targets it already took."""

import os

import asyncpg
import psycopg2
import pytest

from db import migrate

_INSERT = (
    "INSERT INTO route_performance_standards (agency_id, route_code, metric_type, threshold_value, bonus_malus_rate) "
    "VALUES ($1, $2, $3, $4, 0)"
)


@pytest.mark.asyncio
@pytest.mark.parametrize("threshold", [0.0, 85.0, 100.0])
async def test_an_on_time_target_between_0_and_100_is_accepted(aconn, aagency_id, threshold):
    await aconn.execute(_INSERT, aagency_id, "R1", "on_time_pct", threshold)
    assert (
        await aconn.fetchval("SELECT count(*) FROM route_performance_standards WHERE metric_type = 'on_time_pct'") == 1
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("threshold", [-0.1, 100.1, float("nan")])
async def test_an_on_time_target_outside_0_to_100_is_rejected(aconn, aagency_id, threshold):
    with pytest.raises(asyncpg.CheckViolationError):
        await aconn.execute(_INSERT, aagency_id, "R1", "on_time_pct", threshold)


@pytest.mark.asyncio
async def test_the_other_metrics_keep_their_own_ranges(aconn, aagency_id):
    """The 0-100 bound is the on-time share's: an excess wait is in seconds."""
    await aconn.execute(_INSERT, aagency_id, "R1", "ewt_sec", 240.0)
    await aconn.execute(_INSERT, aagency_id, "R1", "vehicle_km_delivered_pct", 95.0)


@pytest.mark.asyncio
async def test_the_table_accepts_every_metric_the_admin_editor_writes(aconn, aagency_id):
    from api.agency_diagnostics import METRIC_TYPES

    for metric_type in METRIC_TYPES:
        await aconn.execute(_INSERT, aagency_id, "R1", metric_type, 50.0)
    assert await aconn.fetchval("SELECT count(*) FROM route_performance_standards") == len(METRIC_TYPES)


@pytest.mark.asyncio
async def test_an_unknown_metric_is_still_rejected(aconn, aagency_id):
    with pytest.raises(asyncpg.CheckViolationError):
        await aconn.execute(_INSERT, aagency_id, "R1", "punctuality", 85.0)


def test_the_down_is_not_marked_destructive():
    down = migrate._MIGRATIONS_DIR / "0090_performance_standard_on_time.down.sql"
    assert not migrate.is_destructive_down(down.read_text())


def _insert(conn, agency_id: int, route_code: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO route_performance_standards "
            "(agency_id, route_code, metric_type, threshold_value, bonus_malus_rate) "
            "VALUES (%s, %s, 'on_time_pct', 85, 0)",
            (agency_id, route_code),
        )


def test_rolling_back_keeps_the_on_time_targets_and_refuses_new_ones(apply_schema):
    """The narrower check comes back for new rows only, so a rollback loses no
    target an admin set, and migrating up again accepts new ones."""
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    agency_id = None
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO agencies (agency_name, feed_url) VALUES ('0090', 'http://0090.example.com') "
                "RETURNING agency_id"
            )
            agency_id = cur.fetchone()[0]
        _insert(conn, agency_id, "KEPT")
        conn.commit()

        migrate.migrate_down("0089", conn)
        with conn.cursor() as cur:
            cur.execute("SELECT route_code FROM route_performance_standards WHERE agency_id = %s", (agency_id,))
            assert cur.fetchall() == [("KEPT",)]
        with pytest.raises(psycopg2.errors.CheckViolation):
            _insert(conn, agency_id, "NEW")
        conn.rollback()

        migrate.migrate_up(conn)
        _insert(conn, agency_id, "NEW")
        conn.commit()
    finally:
        conn.rollback()
        if agency_id is not None:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM route_performance_standards WHERE agency_id = %s", (agency_id,))
                cur.execute("DELETE FROM agencies WHERE agency_id = %s", (agency_id,))
            conn.commit()
        conn.close()
