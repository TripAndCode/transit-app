"""A table's rendered builder may change only with its version.

`tests/unit/agg_registry_versions.json` records, per table, the version and a
hash of `render()`. When a builder's output-deciding text changes:

- if the change can alter any stored row, bump the table's version in
  pipeline/agg_registry.py and pipeline.analyze.ANALYZE_LOGIC_VERSION, which is
  what makes analyze rebuild already-built dates, then run
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

from pipeline import agg_registry, ch_aggregates, db, headways

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


def _verdict(name: str, recorded: dict, version: int, digest: str) -> str | None:
    """Why *name*'s current (version, digest) does not match its record, or None."""
    if version < recorded["version"]:
        return f"{name}'s version went backwards ({recorded['version']} recorded, {version} now)."
    if digest != recorded["hash"] and version == recorded["version"]:
        return (
            f"{name}'s builder changed (render() differs from {RECORD.name}) but its version is still "
            f"{version}. If stored rows can differ, bump its version in pipeline/agg_registry.py and "
            f"pipeline.analyze.ANALYZE_LOGIC_VERSION; either way, then run `{UPDATE}`."
        )
    if (version, digest) != (recorded["version"], recorded["hash"]):
        return f"{RECORD.name} is stale for {name}: run `{UPDATE}` and commit it with the change."
    return None


def test_an_unchanged_builder_passes():
    assert _verdict("t", {"version": 1, "hash": "h"}, 1, "h") is None


def test_a_changed_builder_without_a_bump_fails():
    assert "bump its version" in _verdict("t", {"version": 1, "hash": "h"}, 1, "h2")


def test_a_bump_left_out_of_the_record_fails():
    assert "stale" in _verdict("t", {"version": 1, "hash": "h"}, 2, "h2")
    assert "stale" in _verdict("t", {"version": 1, "hash": "h"}, 2, "h")


def test_a_version_going_backwards_fails():
    assert "backwards" in _verdict("t", {"version": 2, "hash": "h"}, 1, "h")


@pytest.mark.parametrize("name", sorted(agg_registry.TABLES))
def test_each_builder_matches_its_record(name):
    recorded = json.loads(RECORD.read_text())[name]
    verdict = _verdict(name, recorded, agg_registry.TABLES[name].version, _hash(name))
    assert verdict is None, verdict


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


@pytest.mark.parametrize("name", ["agg_feed_health", "agg_route_headway_daily"])
def test_a_clamp_bound_as_a_parameter_reaches_the_hash(monkeypatch, name):
    before = _hash(name)
    monkeypatch.setattr(db, "MAX_PLAUSIBLE_DELAY_SEC", db.MAX_PLAUSIBLE_DELAY_SEC // 2)
    assert _hash(name) != before


@pytest.mark.parametrize("name", ch_aggregates.MONTHLY_TABLES)
def test_monthly_tables_render_the_copy_forward_an_incremental_build_runs(name):
    import inspect

    assert inspect.getsource(ch_aggregates._build_month) in agg_registry.TABLES[name].render()


@pytest.mark.parametrize("name", ch_aggregates.WHOLE_HISTORY_TABLES)
def test_whole_history_tables_render_the_month_stitching_the_build_runs(name):
    import inspect

    assert inspect.getsource(ch_aggregates._internal_source) in agg_registry.TABLES[name].render()


if __name__ == "__main__":
    if "--update" in sys.argv:
        RECORD.write_text(json.dumps(_current(), indent=2, sort_keys=True) + "\n")
    elif "--print" in sys.argv:
        print(json.dumps(_current()))
