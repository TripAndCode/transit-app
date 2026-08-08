"""Applies the ClickHouse schema for `updates`. One table, no migration
chain needed yet — CREATE TABLE IF NOT EXISTS is naturally idempotent.
If this schema needs to evolve later, add versioned migrations then;
building that machinery now for a single table is premature.

NOTE: the live dev ClickHouse instance (~575M-row real-data `updates` table)
predates the trip_id/scheduled_time LowCardinality change in schema.sql and
still has the old String/Nullable(String) types — apply_schema only affects
newly created tables (fresh dev setups, CI, future prod), it does NOT alter
the existing live table. Migrating the live table's column types would need
a separate, deliberate one-time run of:
    ALTER TABLE updates
        MODIFY COLUMN trip_id LowCardinality(String),
        MODIFY COLUMN scheduled_time LowCardinality(Nullable(String))
(this rewrites all existing parts) — intentionally not automated here.
"""

import pathlib

SCHEMA_PATH = pathlib.Path(__file__).parent / "schema.sql"


def apply_schema(client) -> None:
    """Run every `;`-separated statement in schema.sql against *client*."""
    sql = SCHEMA_PATH.read_text()
    for statement in filter(None, (s.strip() for s in sql.split(";"))):
        client.command(statement)
