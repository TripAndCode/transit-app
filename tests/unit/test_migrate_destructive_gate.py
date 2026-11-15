"""Pure-logic tests for the destructive-migration gates in `db.migrate`.

`is_destructive_down` reads only file text (no DB). `migrate_down`'s and,
on a database with a retention floor, `migrate_up`'s refusals are exercised
through a fake connection/cursor that records executed SQL instead of running
it, with `_MIGRATIONS_DIR` pointed at a `tmp_path` so no real migration files
or database are involved.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from db import migrate as migrate_module
from db.migrate import (
    ALLOW_DESTRUCTIVE_UP_ENV,
    DestructiveMigrationError,
    allowed_destructive_ups,
    data_losing_tables,
    is_destructive_down,
    migrate_down,
    migrate_up,
)


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

    def fetchone(self) -> tuple:
        if "to_regclass" in self._last_sql:
            return ("updates_pruned_months" if self._conn.floor else None,)
        if "EXISTS (SELECT 1 FROM updates_pruned_months)" in self._last_sql:
            return (self._conn.floor,)
        raise AssertionError(f"unexpected fetchone after {self._last_sql!r}")


class _FakeConn:
    """Records executed SQL and tracks applied versions. No database."""

    def __init__(self, applied: set[str], *, floor: bool = False):
        self.applied = set(applied)
        self.floor = floor
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


def _write_up(tmp_path: Path, version: str, up_sql: str) -> None:
    (tmp_path / f"{version}_x.up.sql").write_text(up_sql)
    (tmp_path / f"{version}_x.down.sql").write_text("SELECT 1;\n")


@pytest.fixture
def agg_purge(tmp_path, monkeypatch):
    """0001 applied, 0002 pending and deleting a rebuildable aggregate's rows."""
    monkeypatch.setattr(migrate_module, "_MIGRATIONS_DIR", tmp_path)
    migrate_module.reset_for_tests()
    _write_up(tmp_path, "0001", "SELECT 1;\n")
    _write_up(tmp_path, "0002", "DELETE FROM agg_route_daily;\n")
    yield
    migrate_module.reset_for_tests()


def _applied_purge(conn: _FakeConn) -> bool:
    return any("DELETE FROM agg_route_daily" in sql for sql in conn.executed)


def test_with_a_floor_a_pending_up_that_loses_data_is_refused_and_nothing_applies(agg_purge):
    conn = _FakeConn(applied={"0001"}, floor=True)
    with pytest.raises(DestructiveMigrationError) as refused:
        migrate_up(conn)
    assert not _applied_purge(conn)
    message = str(refused.value)
    assert "0002_x.up.sql (agg_route_daily)" in message
    assert "Nothing has been applied" in message
    assert ALLOW_DESTRUCTIVE_UP_ENV in message


def test_without_a_floor_the_same_up_applies(agg_purge):
    conn = _FakeConn(applied={"0001"}, floor=False)
    migrate_up(conn)
    assert _applied_purge(conn)


def test_a_named_version_applies_on_a_floor(agg_purge):
    conn = _FakeConn(applied={"0001"}, floor=True)
    migrate_up(conn, allow_destructive={"0002"})
    assert _applied_purge(conn)


def test_a_version_named_in_the_environment_applies_on_a_floor(agg_purge):
    allowed = allowed_destructive_ups([], {ALLOW_DESTRUCTIVE_UP_ENV: " 0002 , 0009,"})
    assert allowed == {"0002", "0009"}
    conn = _FakeConn(applied={"0001"}, floor=True)
    migrate_up(conn, allow_destructive=allowed)
    assert _applied_purge(conn)


def test_the_flag_and_the_environment_add_up():
    assert allowed_destructive_ups(["0003"], {ALLOW_DESTRUCTIVE_UP_ENV: "0004"}) == {"0003", "0004"}
    assert allowed_destructive_ups([], {}) == frozenset()


def test_the_cli_exits_non_zero_naming_the_refusal(agg_purge, monkeypatch):
    import argparse

    import gtfs_pipeline

    conn = _FakeConn(applied={"0001"}, floor=True)
    conn.close = lambda: None  # type: ignore[attr-defined]
    monkeypatch.setattr(gtfs_pipeline, "_get_conn", lambda require_schema: conn)
    monkeypatch.delenv(ALLOW_DESTRUCTIVE_UP_ENV, raising=False)
    with pytest.raises(SystemExit) as exited:
        gtfs_pipeline.cmd_migrate(argparse.Namespace(direction="up", allow_destructive_up=[], target=None))
    assert "0002_x.up.sql (agg_route_daily)" in str(exited.value.code)
    assert not _applied_purge(conn)

    monkeypatch.setenv(ALLOW_DESTRUCTIVE_UP_ENV, "0002")
    gtfs_pipeline.cmd_migrate(argparse.Namespace(direction="up", allow_destructive_up=[], target=None))
    assert _applied_purge(conn)


def test_with_a_floor_an_unmarked_down_dropping_an_aggregate_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(migrate_module, "_MIGRATIONS_DIR", tmp_path)
    _write_migration(tmp_path, "0001", "DROP TABLE IF EXISTS agg_route_band_daily;\n")
    conn = _FakeConn(applied={"0001"}, floor=True)
    with pytest.raises(DestructiveMigrationError) as refused:
        migrate_down(None, conn)
    assert "0001_x.down.sql" in str(refused.value)
    assert not any("DROP TABLE IF EXISTS agg_route_band_daily" in sql for sql in conn.executed)


def test_with_a_floor_force_rolls_the_down_back(tmp_path, monkeypatch):
    monkeypatch.setattr(migrate_module, "_MIGRATIONS_DIR", tmp_path)
    _write_migration(tmp_path, "0001", "DROP TABLE IF EXISTS agg_route_band_daily;\n")
    conn = _FakeConn(applied={"0001"}, floor=True)
    migrate_down(None, conn, force_destructive=True)
    assert any("DROP TABLE IF EXISTS agg_route_band_daily" in sql for sql in conn.executed)


def test_without_a_floor_that_down_rolls_back(tmp_path, monkeypatch):
    monkeypatch.setattr(migrate_module, "_MIGRATIONS_DIR", tmp_path)
    _write_migration(tmp_path, "0001", "DROP TABLE IF EXISTS agg_route_band_daily;\n")
    conn = _FakeConn(applied={"0001"}, floor=False)
    migrate_down(None, conn)
    assert any("DROP TABLE IF EXISTS agg_route_band_daily" in sql for sql in conn.executed)


def test_0101s_down_is_one_a_floor_refuses():
    down = next(migrate_module._MIGRATIONS_DIR.glob("0101_*.down.sql")).read_text()
    assert data_losing_tables(down) == []
    assert data_losing_tables(down, retention_floor=True) == ["agg_route_band_daily"]
