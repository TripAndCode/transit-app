"""`db.migrate.retention_floor_exists` reads whether any agency has a
retention floor, which makes the rebuildable aggregates lose data to a
migration (`db.migrate.data_losing_tables`)."""

from __future__ import annotations

from db.migrate import retention_floor_exists


def _record_drop(pg_conn, agency_id: int) -> None:
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO updates_pruned_months (partition_id, agency_ids, retention_floor, rows, bytes) "
            "VALUES ('202501', %s, '2025-02-01', 10, 100)",
            ([agency_id],),
        )
    pg_conn.commit()


def test_no_recorded_drop_is_no_floor(pg_conn):
    assert retention_floor_exists(pg_conn) is False


def test_a_recorded_drop_is_a_floor(pg_conn, agency_id):
    _record_drop(pg_conn, agency_id)
    assert retention_floor_exists(pg_conn) is True


def test_a_database_without_the_drops_table_has_no_floor(pg_conn, agency_id):
    _record_drop(pg_conn, agency_id)
    with pg_conn.cursor() as cur:
        cur.execute("CREATE SCHEMA unmigrated")
        cur.execute("SET LOCAL search_path TO unmigrated")
    try:
        assert retention_floor_exists(pg_conn) is False
    finally:
        pg_conn.rollback()
