"""`tests/unit` is the suite that runs with no database at all. A test here
that needs ClickHouse skips silently without RUN_CH_INTEGRATION=1, which reads
as a pass; such tests live under `tests/clickhouse/`."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.unit.conftest import clickhouse_gated

UNIT_DIR = Path(__file__).resolve().parent


def test_a_test_requesting_the_clickhouse_fixture_is_gated():
    assert clickhouse_gated(["pg_conn", "ch_client"], []) is True
    assert clickhouse_gated(["ch_async_client"], []) is True


def test_a_test_skipped_for_want_of_ch_test_is_gated():
    assert clickhouse_gated([], ["requires `make ch-test`"]) is True
    assert clickhouse_gated([], ["RUN_CH_INTEGRATION=1 not set"]) is True


def test_a_pure_test_is_not_gated():
    assert clickhouse_gated(["monkeypatch", "tmp_path"], ["RUN_SLOW=1 not set"]) is False


def test_the_clickhouse_directory_exists_and_skips_postgres():
    conftest = UNIT_DIR.parent / "clickhouse" / "conftest.py"
    assert conftest.exists()
    assert "def apply_schema" in conftest.read_text()


def _imports_the_driver(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            modules = [node.module or ""]
        else:
            continue
        if any(m == "clickhouse_connect" or m.startswith("clickhouse_connect.") for m in modules):
            return True
    return False


def test_no_module_under_tests_unit_imports_the_clickhouse_driver():
    """A gate worded any other way is still a test that builds a client. The
    conftest refuses that at run time; a module importing the driver itself
    could bind the real constructor before that refusal is in place."""
    offenders = sorted(
        str(p.relative_to(UNIT_DIR))
        for p in UNIT_DIR.rglob("*.py")
        if p.name != "conftest.py" and _imports_the_driver(ast.parse(p.read_text()))
    )
    assert offenders == [], (
        f"tests/unit modules importing clickhouse_connect; move them to tests/clickhouse/: {offenders}"
    )


def test_a_clickhouse_client_cannot_be_built_under_tests_unit(monkeypatch):
    from pipeline.clickhouse import get_client

    for name in ("CLICKHOUSE_USER", "CLICKHOUSE_PASSWORD", "CLICKHOUSE_DATABASE"):
        monkeypatch.setenv(name, "x")
    with pytest.raises(pytest.fail.Exception, match="tests/clickhouse"):
        get_client()
