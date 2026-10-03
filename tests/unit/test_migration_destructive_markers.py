"""Every down migration that drops or deletes data outside the rebuildable
`agg_*` tables must carry the `-- DESTRUCTIVE` line `db.migrate` gates on,
or `migrate down` runs it without being asked twice."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from db.migrate import _MIGRATIONS_DIR, is_destructive_down

_COMMENT = re.compile(r"--[^\n]*")
_IDENT = r"[A-Za-z_][A-Za-z0-9_]*"
_DATA_LOSS = re.compile(
    rf"^\s*(?:DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?P<drop>{_IDENT})"
    rf"|DELETE\s+FROM\s+(?P<delete>{_IDENT})"
    rf"|TRUNCATE\s+(?:TABLE\s+)?(?:ONLY\s+)?(?P<truncate>{_IDENT})"
    rf"|ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?P<alter>{_IDENT})\b.*?\bDROP\s+COLUMN\b)",
    re.IGNORECASE | re.DOTALL,
)


def data_losing_tables(sql: str) -> list[str]:
    """Non-`agg_*` tables a down migration drops rows or columns from."""
    tables: list[str] = []
    for statement in _COMMENT.sub("", sql).split(";"):
        m = _DATA_LOSS.match(statement)
        if not m:
            continue
        name = m.group("drop") or m.group("delete") or m.group("truncate") or m.group("alter")
        if not name.lower().startswith("agg_"):
            tables.append(name)
    return tables


def test_classifier_counts_tables_columns_and_rows_but_not_indexes_or_aggs():
    assert data_losing_tables("DROP TABLE IF EXISTS users;\n") == ["users"]
    assert data_losing_tables("ALTER TABLE static_trips\n    DROP COLUMN IF EXISTS service_id;\n") == ["static_trips"]
    assert data_losing_tables(
        "DELETE FROM login_events WHERE kind = 'x';\nALTER TABLE login_events DROP CONSTRAINT c;"
    ) == ["login_events"]
    assert (
        data_losing_tables(
            "DROP INDEX IF EXISTS idx_x;\nDROP TRIGGER IF EXISTS t ON api_keys;\nDROP FUNCTION IF EXISTS f();"
        )
        == []
    )
    assert (
        data_losing_tables("DROP TABLE IF EXISTS agg_route_daily;\nALTER TABLE agg_meta DROP COLUMN IF EXISTS x;") == []
    )
    assert data_losing_tables("ALTER TABLE agg_route_dow DROP CONSTRAINT agg_route_dow_pkey;") == []
    assert data_losing_tables("-- also mentions DROP TABLE users in prose\nSELECT 1;") == []


DOWN_FILES = sorted(_MIGRATIONS_DIR.glob("*.down.sql"))


def test_every_migration_has_a_down_file():
    assert len(DOWN_FILES) >= 66


@pytest.mark.parametrize("path", DOWN_FILES, ids=lambda p: p.name)
def test_a_data_losing_down_migration_carries_the_marker(path: Path):
    sql = path.read_text()
    lost = data_losing_tables(sql)
    if lost:
        assert is_destructive_down(sql), f"{path.name} loses data in {lost} but has no `-- DESTRUCTIVE` line"
