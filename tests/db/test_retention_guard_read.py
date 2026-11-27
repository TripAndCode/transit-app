"""pipeline.recorded_floor.retention_floor_async reads the floor the live readers
refuse before (pipeline.live_filing.require_retained) on an asyncpg
connection, exactly as retention_floor reads it."""

from __future__ import annotations

from datetime import date

from pipeline.recorded_floor import retention_floor_async
from pipeline.retention import retention_floor


async def _record_drop(aconn, agency_id: int, floor: date) -> None:
    await aconn.execute(
        "INSERT INTO updates_pruned_months (partition_id, agency_ids, retention_floor, rows, bytes) "
        "VALUES ('202501', $1, $2, 10, 100)",
        [agency_id],
        floor,
    )


async def test_no_recorded_drop_is_no_floor(aconn, aagency_id):
    assert await retention_floor_async(aconn, aagency_id) is None


async def test_the_floor_reads_as_the_sync_read_api_reads_it(aconn, aagency_id, pg_conn):
    """A floor recorded mid-month reads back rounded to the next month's
    first, as retention_floors rounds it."""
    await _record_drop(aconn, aagency_id, date(2025, 2, 2))
    assert await retention_floor_async(aconn, aagency_id) == date(2025, 3, 1) == retention_floor(pg_conn, aagency_id)


async def test_another_agencys_floor_is_not_this_ones(aconn, aagency_id):
    await _record_drop(aconn, aagency_id + 1000, date(2025, 3, 1))
    assert await retention_floor_async(aconn, aagency_id) is None


async def test_a_database_without_the_drops_table_has_no_floor_and_keeps_its_transaction(aconn, aagency_id):
    await _record_drop(aconn, aagency_id, date(2025, 3, 1))
    async with aconn.transaction():
        await aconn.execute("SET LOCAL search_path TO pg_catalog")
        assert await retention_floor_async(aconn, aagency_id) is None
        assert await aconn.fetchval("SELECT 1") == 1
