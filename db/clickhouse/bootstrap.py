"""Applies the ClickHouse schema for `updates`. One table, no migration
chain needed yet — CREATE TABLE IF NOT EXISTS is naturally idempotent.
If this schema needs to evolve later, add versioned migrations then;
building that machinery now for a single table is premature.

NOTE: the live dev ClickHouse instance (a large, real-data `updates` table)
predates the trip_id/scheduled_time LowCardinality change in schema.sql and
still has the old String/Nullable(String) types — apply_schema only affects
newly created tables (fresh dev setups, CI, future prod), it does NOT alter
the existing live table. Migrating the live table's column types would need
a separate, deliberate one-time run of:
    ALTER TABLE updates MODIFY SETTING allow_nullable_key = 1;
    ALTER TABLE updates
        MODIFY COLUMN trip_id LowCardinality(String),
        MODIFY COLUMN scheduled_time LowCardinality(Nullable(String)),
        MODIFY COLUMN route_code LowCardinality(Nullable(String))
(this rewrites all existing parts) — intentionally not automated here. The
`MODIFY SETTING` must run first: route_code sits in the table's ORDER BY, and
ClickHouse rejects a Nullable sort-key column while `allow_nullable_key` is
off.

route_code is Nullable because Postgres's `updates.route_code` was nullable
(migration 0006 dropped its NOT NULL) — both the static_join and aomori_regex
ingest strategies can produce a row with no resolvable route. A non-nullable
column here would reject those rows outright (DataError on insert), silently
losing whole files instead of the row-level gap Postgres tolerated.
"""

import pathlib

SCHEMA_PATH = pathlib.Path(__file__).parent / "schema.sql"


def apply_schema(client) -> None:
    """Run every `;`-separated statement in schema.sql against *client*."""
    sql = SCHEMA_PATH.read_text()
    for statement in filter(None, (s.strip() for s in sql.split(";"))):
        client.command(statement)
