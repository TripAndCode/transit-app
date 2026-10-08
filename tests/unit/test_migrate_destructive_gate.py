"""Pure-logic tests for the destructive-down-migration gate in `db.migrate`.

`is_destructive_down` reads only file text (no DB). `migrate_down`'s refusal
is exercised through a fake connection/cursor that records executed SQL
instead of running it, with `_MIGRATIONS_DIR` pointed at a `tmp_path` so no
real migration files or database are involved.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from db import migrate as migrate_module
from db.migrate import DestructiveMigrationError, is_destructive_down, migrate_down


def test_is_destructive_down_detects_marker_line():
    sql = "-- DESTRUCTIVE: drops the audit table, losing its history\nDROP TABLE foo;\n"
    assert is_destructive_down(sql) is True


def test_is_destructive_down_false_without_marker():
    sql = "-- an ordinary comment\nDROP TABLE foo;\n"
    assert is_destructive_down(sql) is False


def test_is_destructive_down_false_for_empty_file():
    assert is_destructive_down("") is False


def test_is_destructive_down_ignores_marker_appearing_mid_line():
    # Must be its own comment line, not text that merely contains the phrase.
    sql = "-- see -- DESTRUCTIVE note in 0001 for background\nDROP TABLE foo;\n"
    assert is_destructive_down(sql) is False


class _FakeCursor:
    def __init__(self, conn: "_FakeConn"):
        self._conn = conn
        self._last_sql = ""

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def execute(self, sql: str, params: tuple | None = None) -> None:
        self._conn.executed.append(sql)
        self._last_sql = sql

    def fetchall(self) -> list[tuple[str]]:
        if "SELECT version FROM schema_migrations" in self._last_sql:
            return [(v,) for v in sorted(self._conn.applied)]
        return []


class _FakeConn:
    """Records executed SQL and tracks applied versions. No database."""

    def __init__(self, applied: set[str]):
        self.applied = set(applied)
        self.executed: list[str] = []
        self.committed = 0
        self.rolled_back = 0

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self)

    def commit(self) -> None:
        self.committed += 1

    def rollback(self) -> None:
        self.rolled_back += 1


def _write_migration(tmp_path: Path, version: str, down_sql: str) -> None:
    (tmp_path / f"{version}_x.up.sql").write_text("SELECT 1;\n")
    (tmp_path / f"{version}_x.down.sql").write_text(down_sql)


def test_migrate_down_refuses_destructive_without_force(tmp_path, monkeypatch):
    monkeypatch.setattr(migrate_module, "_MIGRATIONS_DIR", tmp_path)
    _write_migration(tmp_path, "0001", "-- DESTRUCTIVE: drops stuff, unrecoverable\nDROP TABLE stuff;\n")
    conn = _FakeConn(applied={"0001"})

    with pytest.raises(DestructiveMigrationError):
        migrate_down(None, conn)

    assert not any("DROP TABLE stuff" in sql for sql in conn.executed)
    assert conn.rolled_back == 0


def test_migrate_down_runs_destructive_with_force(tmp_path, monkeypatch):
    monkeypatch.setattr(migrate_module, "_MIGRATIONS_DIR", tmp_path)
    _write_migration(tmp_path, "0001", "-- DESTRUCTIVE: drops stuff, unrecoverable\nDROP TABLE stuff;\n")
    conn = _FakeConn(applied={"0001"})

    migrate_down(None, conn, force_destructive=True)

    assert any("DROP TABLE stuff" in sql for sql in conn.executed)
    assert conn.committed >= 1


def test_migrate_down_runs_non_destructive_without_force(tmp_path, monkeypatch):
    monkeypatch.setattr(migrate_module, "_MIGRATIONS_DIR", tmp_path)
    _write_migration(tmp_path, "0001", "DROP TABLE stuff;\n")
    conn = _FakeConn(applied={"0001"})

    migrate_down(None, conn)

    assert any("DROP TABLE stuff" in sql for sql in conn.executed)
    assert conn.committed >= 1


def test_readme_documents_the_marker():
    readme = Path(__file__).resolve().parents[2] / "db" / "migrations" / "README.md"
    assert readme.exists(), "db/migrations/README.md is missing"
    text = readme.read_text()
    assert "-- DESTRUCTIVE" in text


def test_a_range_spanning_a_destructive_migration_rolls_back_nothing(tmp_path, monkeypatch):
    """The refusal has to land before the first rollback, not at the marked one.

    Each down migration commits on its own, so checking one version at a time
    inside the loop would leave everything above the marked one already rolled
    back -- a schema the operator never asked for, reachable again only by
    migrating forward.
    """
    monkeypatch.setattr(migrate_module, "_MIGRATIONS_DIR", tmp_path)
    _write_migration(tmp_path, "0001", "DROP TABLE stuff_a;\n")
    _write_migration(tmp_path, "0002", "-- DESTRUCTIVE: drops a column\nDROP TABLE stuff_b;\n")
    _write_migration(tmp_path, "0003", "DROP TABLE stuff_c;\n")
    conn = _FakeConn(applied={"0001", "0002", "0003"})

    with pytest.raises(DestructiveMigrationError) as excinfo:
        migrate_down("0000", conn)

    assert not any("DROP TABLE stuff" in sql for sql in conn.executed), "nothing may run before the refusal"
    assert "0002_x.down.sql" in str(excinfo.value)
    assert "Nothing has been rolled back" in str(excinfo.value)


def test_the_refusal_names_every_marked_version_in_the_range(tmp_path, monkeypatch):
    """One re-run should surface all of them, not just the nearest."""
    monkeypatch.setattr(migrate_module, "_MIGRATIONS_DIR", tmp_path)
    _write_migration(tmp_path, "0001", "-- DESTRUCTIVE: a\nDROP TABLE stuff_a;\n")
    _write_migration(tmp_path, "0002", "DROP TABLE stuff_b;\n")
    _write_migration(tmp_path, "0003", "-- DESTRUCTIVE: c\nDROP TABLE stuff_c;\n")
    conn = _FakeConn(applied={"0001", "0002", "0003"})

    with pytest.raises(DestructiveMigrationError) as excinfo:
        migrate_down("0000", conn)

    message = str(excinfo.value)
    assert "0001_x.down.sql" in message and "0003_x.down.sql" in message


def test_a_forced_range_rolls_back_every_version_in_it(tmp_path, monkeypatch):
    monkeypatch.setattr(migrate_module, "_MIGRATIONS_DIR", tmp_path)
    _write_migration(tmp_path, "0001", "DROP TABLE stuff_a;\n")
    _write_migration(tmp_path, "0002", "-- DESTRUCTIVE: drops a column\nDROP TABLE stuff_b;\n")
    conn = _FakeConn(applied={"0001", "0002"})

    migrate_down("0000", conn, force_destructive=True)

    assert any("DROP TABLE stuff_a" in sql for sql in conn.executed)
    assert any("DROP TABLE stuff_b" in sql for sql in conn.executed)
