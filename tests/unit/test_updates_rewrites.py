"""Whatever rewrites `updates` rows in place writes off the refiled rows
analyze wrote for their agencies first (pipeline.refiled.rows_rewritten): a
rewrite can keep a date's row count and version names, which a read's check
of the written rows cannot see."""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# A swap of a whole table, or a mutation, partition drop or lightweight delete of `updates`.
_REWRITE = re.compile(
    r"EXCHANGE\s+TABLES|ALTER\s+TABLE\s+\{?\w*updates\w*\}?\s+(?:UPDATE|DELETE|DROP\s+PARTITION)"
    r"|DELETE\s+FROM\s+\{?\w*updates\w*\}?\b",
    re.IGNORECASE,
)


def _sources() -> list[Path]:
    listed = subprocess.run(["git", "ls-files", "*.py"], cwd=ROOT, capture_output=True, text=True, check=True)
    return [ROOT / name for name in listed.stdout.split() if not name.startswith("tests/")]


def test_every_rewrite_of_updates_writes_off_the_refiled_rows_first():
    rewriting = [path for path in _sources() if _REWRITE.search(path.read_text(encoding="utf-8"))]
    assert rewriting, "found no rewrite of `updates`: the pattern no longer matches the swaps"
    unhooked = [str(path.relative_to(ROOT)) for path in rewriting if "refiled.rows_rewritten(" not in path.read_text()]
    assert not unhooked, f"rewrite `updates` without pipeline.refiled.rows_rewritten: {unhooked}"


def test_the_pattern_finds_each_kind_of_rewrite():
    for sql in (
        "EXCHANGE TABLES updates AND staging",
        "ALTER TABLE updates DELETE WHERE 1",
        "ALTER TABLE {UPDATES_TABLE} UPDATE x = 1 WHERE 1",
        "DELETE FROM updates WHERE agency_id = 1",
        "ALTER TABLE updates DROP PARTITION ID '202401'",
        "ALTER TABLE {UPDATES_TABLE} DROP PARTITION 202401",
    ):
        assert _REWRITE.search(sql), sql
    assert not _REWRITE.search("ALTER TABLE updates MODIFY SETTING allow_nullable_key = 1")
    assert not _REWRITE.search("ALTER TABLE {table} DROP PARTITION {partition}")
