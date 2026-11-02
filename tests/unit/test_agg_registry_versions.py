"""A table's rendered builder may change only with its version.

`tests/unit/agg_registry_versions.json` records, per table, the version and a
hash of `render()`. When a builder's output-deciding text changes:

- if the change can alter any stored row, bump the table's version in
  pipeline/agg_registry.py, then run
  `poetry run python -m tests.unit.test_agg_registry_versions --update`;
- if it provably cannot (a comment inside SQL, a pure refactor), run `--update`
  alone and say why in the commit message.
"""

import hashlib
import json
import pathlib
import subprocess
import sys

import pytest

from pipeline import agg_registry, headways

RECORD = pathlib.Path(__file__).with_name("agg_registry_versions.json")
REPO = RECORD.parents[2]
UPDATE = "poetry run python -m tests.unit.test_agg_registry_versions --update"


def _hash(name: str) -> str:
    return hashlib.sha256("\x1e".join(agg_registry.TABLES[name].render()).encode()).hexdigest()


def _current() -> dict:
    return {
        name: {"version": agg_registry.TABLES[name].version, "hash": _hash(name)}
        for name in sorted(agg_registry.TABLES)
    }


def test_every_registered_table_is_recorded():
    assert set(json.loads(RECORD.read_text())) == set(agg_registry.TABLES)


@pytest.mark.parametrize("name", sorted(agg_registry.TABLES))
def test_a_changed_builder_comes_with_a_version_bump(name):
    recorded = json.loads(RECORD.read_text())[name]
    table = agg_registry.TABLES[name]
    assert table.version >= recorded["version"], f"{name}'s version went backwards"
    if _hash(name) != recorded["hash"]:
        assert table.version > recorded["version"], (
            f"{name}'s builder changed (render() differs from {RECORD.name}) but its version is still "
            f"{table.version}. If stored rows can differ, bump its version in pipeline/agg_registry.py; "
            f"either way, then run `{UPDATE}`."
        )


def test_rendering_is_deterministic_within_a_process():
    assert _current() == _current()


def test_rendering_is_deterministic_across_processes():
    out = subprocess.run(
        [sys.executable, "-m", "tests.unit.test_agg_registry_versions", "--print"],
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO,
    ).stdout
    assert json.loads(out) == _current()


def test_a_helper_constant_reaches_the_hash(monkeypatch):
    before = _hash("agg_route_headway_daily")
    monkeypatch.setattr(headways, "LONG_GAP_MULTIPLIER", headways.LONG_GAP_MULTIPLIER + 0.25)
    assert _hash("agg_route_headway_daily") != before


if __name__ == "__main__":
    if "--update" in sys.argv:
        RECORD.write_text(json.dumps(_current(), indent=2, sort_keys=True) + "\n")
    elif "--print" in sys.argv:
        print(json.dumps(_current()))
