"""`tests/unit` is the suite that runs with no database at all. A test here
that needs ClickHouse skips silently without RUN_CH_INTEGRATION=1, which reads
as a pass; such tests live under `tests/clickhouse/`."""

from __future__ import annotations

import re
from pathlib import Path

from tests.unit.conftest import clickhouse_gated

UNIT_DIR = Path(__file__).resolve().parent
_GATE_IN_SOURCE = re.compile(
    r'skipif\(\s*os\.environ\.get\("RUN_CH_INTEGRATION"\)|\bdef test_\w+\([^)]*\bch_(?:async_)?client\b'
)


def test_a_test_requesting_the_clickhouse_fixture_is_gated():
    assert clickhouse_gated(["pg_conn", "ch_client"], []) is True
    assert clickhouse_gated(["ch_async_client"], []) is True


def test_a_test_skipped_for_want_of_ch_test_is_gated():
    assert clickhouse_gated([], ["requires `make ch-test`"]) is True
    assert clickhouse_gated([], ["RUN_CH_INTEGRATION=1 not set"]) is True


def test_a_pure_test_is_not_gated():
    assert clickhouse_gated(["monkeypatch", "tmp_path"], ["RUN_SLOW=1 not set"]) is False


def test_no_file_under_tests_unit_declares_a_clickhouse_gate():
    offenders = sorted(
        p.name
        for p in UNIT_DIR.glob("test_*.py")
        if p.name != Path(__file__).name and _GATE_IN_SOURCE.search(p.read_text())
    )
    assert offenders == [], f"ClickHouse-gated tests under tests/unit; move them to tests/clickhouse/: {offenders}"


def test_the_clickhouse_directory_exists_and_skips_postgres():
    conftest = UNIT_DIR.parent / "clickhouse" / "conftest.py"
    assert conftest.exists()
    assert "def apply_schema" in conftest.read_text()
