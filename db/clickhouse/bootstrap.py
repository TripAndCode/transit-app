"""Applies the ClickHouse schema for `updates`. One table, no migration
chain needed yet — CREATE TABLE IF NOT EXISTS is naturally idempotent.
If this schema needs to evolve later, add versioned migrations then;
building that machinery now for a single table is premature.
"""

import pathlib

SCHEMA_PATH = pathlib.Path(__file__).parent / "schema.sql"


def apply_schema(client) -> None:
    """Run every `;`-separated statement in schema.sql against *client*."""
    sql = SCHEMA_PATH.read_text()
    for statement in filter(None, (s.strip() for s in sql.split(";"))):
        client.command(statement)
