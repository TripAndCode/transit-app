"""Every down migration that drops or deletes data outside the rebuildable
`agg_*` tables must carry the `-- DESTRUCTIVE` line `db.migrate` gates on,
or `migrate down` runs it without being asked twice."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from db.migrate import _MIGRATIONS_DIR, is_destructive_down

_COMMENT = re.compile(r"--[^\n]*")
_NAME = r'(?:"[^"]+"|[A-Za-z_][A-Za-z0-9_$]*)'
_TABLE = rf"{_NAME}(?:\s*\.\s*{_NAME})?"
_TABLES = rf"{_TABLE}(?:\s*,\s*{_TABLE})*"
_DATA_LOSS = re.compile(
    rf"^\s*(?:DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?P<drop>{_TABLES})"
    rf"|DELETE\s+FROM\s+(?:ONLY\s+)?(?P<delete>{_TABLE})"
    rf"|TRUNCATE\s+(?:TABLE\s+)?(?:ONLY\s+)?(?P<truncate>{_TABLES})"
    rf"|ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?:ONLY\s+)?(?P<alter>{_TABLE})\s.*?\bDROP\s+COLUMN\b)",
    re.IGNORECASE | re.DOTALL,
)


def _table_names(group: str) -> list[str]:
    """Bare table names in a matched (possibly comma-listed, schema-qualified,
    quoted) group."""
    names = re.findall(_TABLE, group)
    return [re.findall(_NAME, name)[-1].strip('"') for name in names]


def data_losing_tables(sql: str) -> list[str]:
    """Non-`agg_*` tables a top-level down-migration statement drops rows or
    columns from.

    Scope: plain DROP TABLE, DELETE FROM, TRUNCATE and ALTER TABLE ... DROP
    COLUMN statements. Statements inside a `DO` block, DROP SCHEMA/EXTENSION/TYPE ...
    CASCADE, and a narrowing ALTER COLUMN ... TYPE are not detected; a down
    file using them needs the marker by the author's own judgment.
    """
    tables: list[str] = []
    for statement in _COMMENT.sub("", sql).split(";"):
        m = _DATA_LOSS.match(statement)
        if not m:
            continue
        group = m.group("drop") or m.group("delete") or m.group("truncate") or m.group("alter")
        tables.extend(name for name in _table_names(group) if not name.lower().startswith("agg_"))
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


def test_classifier_reads_only_comma_lists_and_quoted_or_qualified_names():
    assert data_losing_tables("ALTER TABLE ONLY users DROP COLUMN email;") == ["users"]
    assert data_losing_tables("ALTER TABLE only_users DROP COLUMN email;") == ["only_users"]
    assert data_losing_tables("DROP TABLE agg_route_daily, users;") == ["users"]
    assert data_losing_tables("TRUNCATE sessions, agg_meta, api_keys;") == ["sessions", "api_keys"]
    assert data_losing_tables('DROP TABLE IF EXISTS "Users";') == ["Users"]
    assert data_losing_tables("DELETE FROM ONLY public.login_events WHERE true;") == ["login_events"]
    assert data_losing_tables('ALTER TABLE public."agg_x" DROP COLUMN y;') == []


DOWN_FILES = sorted(_MIGRATIONS_DIR.glob("*.down.sql"))


def _stems(suffix: str) -> set[str]:
    return {p.name.removesuffix(suffix) for p in _MIGRATIONS_DIR.glob(f"*{suffix}")}


def test_every_up_migration_has_a_down_file():
    assert DOWN_FILES
    assert _stems(".up.sql") == _stems(".down.sql")


@pytest.mark.parametrize("path", DOWN_FILES, ids=lambda p: p.name)
def test_a_data_losing_down_migration_carries_the_marker(path: Path):
    sql = path.read_text()
    lost = data_losing_tables(sql)
    if lost:
        assert is_destructive_down(sql), f"{path.name} loses data in {lost} but has no `-- DESTRUCTIVE` line"
