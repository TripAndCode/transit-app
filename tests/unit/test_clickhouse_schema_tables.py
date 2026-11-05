"""db/clickhouse/schema.sql creates exactly the ClickHouse tables the app uses."""

import re
from pathlib import Path

from pipeline import ch_aggregates
from pipeline.clickhouse import LIVE_TABLE, UPDATES_TABLE

SCHEMA = Path(__file__).resolve().parents[2] / "db" / "clickhouse" / "schema.sql"
_CREATE = re.compile(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)", re.IGNORECASE)


def _statements() -> list[str]:
    """The schema's statements as db.clickhouse.bootstrap.apply_schema splits them, comment lines dropped."""
    statements = []
    for raw in SCHEMA.read_text().split(";"):
        code = "\n".join(line for line in raw.splitlines() if not line.lstrip().startswith("--")).strip()
        if code:
            statements.append(code)
    return statements


def test_the_schema_creates_only_the_tables_the_app_uses():
    created = set()
    for statement in _statements():
        match = _CREATE.match(statement)
        assert match, f"not a CREATE TABLE: {statement[:60]!r}"
        created.add(match.group(1))
    assert created == {UPDATES_TABLE, LIVE_TABLE, *ch_aggregates.STORE_TABLES}
