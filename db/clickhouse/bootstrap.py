"""Applies the ClickHouse schema for `updates`. One table, no migration
chain needed yet — CREATE TABLE IF NOT EXISTS is naturally idempotent.
If this schema needs to evolve later, add versioned migrations then;
building that machinery now for a single table is premature.

NOTE: the live dev ClickHouse instance (a large, real-data `updates` table)
predates the trip_id/scheduled_time LowCardinality change in schema.sql and
still has the old String/Nullable(String) types — CREATE TABLE IF NOT EXISTS
only affects newly created tables (fresh dev setups, CI, future prod), it
does NOT alter the existing live table's column *types*. Migrating those
would need a separate, deliberate one-time run of:
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

Adding a brand-new nullable column (e.g. stop_id, arr_delay,
schedule_relationship_trip, schedule_relationship_stop, feed_timestamp) is
different from the type-migration case above: apply_schema runs an
`ALTER TABLE ... ADD COLUMN IF NOT EXISTS` for each entry in
_NEW_NULLABLE_COLUMNS on every call, unconditionally, alongside the
CREATE TABLE IF NOT EXISTS. That statement is idempotent (a no-op once the
column exists) and metadata-only against ClickHouse's MergeTree engine (it
does not rewrite existing parts, unlike the MODIFY COLUMN migration above),
so it's safe to run against a table that already has the column, a table
that doesn't yet, or a table this call is creating from scratch in the same
breath. This is what keeps insert_updates' `column_names=UPDATE_COLUMNS`
working against an already-bootstrapped `updates` table: ClickHouse rejects
an INSERT's entire batch (not just the unrecognized field) if any named
column doesn't exist on the target table, so a schema.sql column added after
`updates` was first created must reach every existing table, not just
brand-new ones.
"""

import pathlib

SCHEMA_PATH = pathlib.Path(__file__).parent / "schema.sql"

# Columns added to `updates` after it was first widely created, each as a
# (name, type) pair. Kept in sync with schema.sql's own CREATE TABLE column
# list by hand (see that file for the authoritative declaration) — this list
# only exists to reach a table that already existed before the column was
# added; a table created fresh from CREATE TABLE IF NOT EXISTS already has
# every column schema.sql declares.
_NEW_NULLABLE_COLUMNS = [
    ("stop_id", "LowCardinality(Nullable(String))"),
    ("arr_delay", "Nullable(Int32)"),
    ("schedule_relationship_trip", "Nullable(UInt8)"),
    ("schedule_relationship_stop", "Nullable(UInt8)"),
    ("feed_timestamp", "Nullable(UInt64)"),
]


def apply_schema(client) -> None:
    """Run every `;`-separated statement in schema.sql against *client*, then
    ensure every column in _NEW_NULLABLE_COLUMNS exists on `updates` —
    self-healing schema evolution regardless of whether `updates` pre-existed
    this call (see module docstring for why the ADD COLUMN step is required
    even when CREATE TABLE IF NOT EXISTS is a no-op).
    """
    sql = SCHEMA_PATH.read_text()
    for statement in filter(None, (s.strip() for s in sql.split(";"))):
        client.command(statement)
    for column, col_type in _NEW_NULLABLE_COLUMNS:
        client.command(f"ALTER TABLE updates ADD COLUMN IF NOT EXISTS {column} {col_type}")
