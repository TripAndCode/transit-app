"""Where each agency's retention floor is recorded, and how a live reader
reads it (pipeline.retention.retention_floor says what the floor means).

Kept free of the pipeline's other modules so that pipeline.retained_reads,
which every cached compute imports through pipeline.cache, can read a floor
without importing pipeline.retention, which imports pipeline.cache.
"""

from __future__ import annotations

from datetime import date, timedelta

#: One row per `updates` month the prune drops, which every retention floor is read from (migration 0104).
PRUNED_TABLE = "updates_pruned_months"

# The newest floor recorded for each agency of a list, bound as {ids}.
FLOORS_SQL = (
    f"SELECT agency_id, max(retention_floor) FROM {PRUNED_TABLE}, unnest(agency_ids) AS agency_id "
    "WHERE agency_id = ANY({ids}) GROUP BY agency_id"
)


def month_start_on_or_after(day: date) -> date:
    if day.day == 1:
        return day
    return (day.replace(day=1) + timedelta(days=32)).replace(day=1)


async def pruned_table_exists(conn) -> bool:
    """Whether the database records drops at all. One not yet migrated to has
    had none. A statement naming a missing table fails before it runs, and
    inside a transaction would abort it, so this is asked first."""
    return bool(await conn.fetchval("SELECT to_regclass($1) IS NOT NULL", PRUNED_TABLE))


async def recorded_floor_async(conn, agency_id: int) -> date | None:
    """The agency's floor on an asyncpg connection, read fresh, from a
    database pruned_table_exists has found recording drops."""
    row = await conn.fetchrow(FLOORS_SQL.format(ids="$1::int[]"), [agency_id])
    return None if row is None else month_start_on_or_after(row[1])


async def retention_floor_async(conn, agency_id: int) -> date | None:
    """retention_floor on an asyncpg connection, read fresh on every call."""
    if not await pruned_table_exists(conn):
        return None
    return await recorded_floor_async(conn, agency_id)
