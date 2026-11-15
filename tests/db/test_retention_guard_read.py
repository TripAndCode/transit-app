"""api.retention_guard reads each agency's floor through the retention read
API (`pipeline.retention.retention_floor`) on a short connection of its own."""

from __future__ import annotations

import os

from api.retention_guard import _read_floor
from pipeline.retention import retention_floor


def _record_drop(pg_conn, agency_id: int) -> None:
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO updates_pruned_months (partition_id, agency_ids, retention_floor, rows, bytes) "
            "VALUES ('202501', %s, '2025-02-01', 10, 100)",
            ([agency_id],),
        )
    pg_conn.commit()


def test_the_api_reads_the_floor_through_the_retention_read_api(pg_conn, agency_id):
    assert _read_floor(agency_id) is None
    _record_drop(pg_conn, agency_id)
    assert _read_floor(agency_id) == retention_floor(pg_conn, agency_id) is not None


def test_the_api_reads_no_floor_from_a_database_without_the_drops_table(pg_conn, agency_id, monkeypatch):
    _record_drop(pg_conn, agency_id)
    url = os.environ["DATABASE_URL"]
    monkeypatch.setenv("DATABASE_URL", url + ("&" if "?" in url else "?") + "options=-csearch_path%3Dunmigrated")
    assert _read_floor(agency_id) is None
