"""The analysis method's version must name exactly the inputs analyze builds with.

`pipeline.agg_registry.METHOD_VERSIONS` records, oldest first, what each method
version set of `method_inputs()`: every table's logic version and the clamp.
A table's version bump (forced by test_agg_registry_versions) or a clamp change
therefore fails here until a new method version records it, so every exported
figure's `method_version` names the method that produced it.
"""

import re
from pathlib import Path

import pytest

from pipeline import agg_registry, db
from pipeline.agg_registry import METHOD_VERSION, METHOD_VERSIONS, MethodVersion, method_inputs

DOC = Path(__file__).parents[2] / "DELAY_ANALYSIS.md"
_VERSION = re.compile(r"^\d{4}\.\d{1,2}\.\d+$")


def _fold(history: tuple[MethodVersion, ...]) -> dict[str, int]:
    inputs: dict[str, int] = {}
    for entry in history:
        for name, value in entry.sets.items():
            if value is None:
                inputs.pop(name, None)
            else:
                inputs[name] = value
    return inputs


def _verdict(history: tuple[MethodVersion, ...], live: dict[str, int]) -> str | None:
    """Why *live* is not the method *history*'s newest version records, or None."""
    recorded = _fold(history)
    if recorded == live:
        return None
    moved = sorted(name for name in recorded.keys() | live.keys() if recorded.get(name) != live.get(name))
    return (
        f"The method's inputs moved since {history[-1].version} ({', '.join(moved)}). Append a MethodVersion "
        "to METHOD_VERSIONS in pipeline/agg_registry.py with a later version and what it sets, and add its row "
        f'to the "Method versions" table in {DOC.name}. Never edit a recorded version: exports name it.'
    )


def _key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def _doc_versions() -> list[str]:
    section = DOC.read_text().split("\n## Method versions\n", 1)[1].split("\n## ", 1)[0]
    rows = [line for line in section.splitlines() if line.startswith("|")]
    # The header row and the separator row come first.
    return [row.split("|")[1].strip().strip("`") for row in rows[2:]]


def test_the_newest_method_version_records_the_live_inputs():
    verdict = _verdict(METHOD_VERSIONS, method_inputs())
    assert verdict is None, verdict


def test_method_inputs_are_every_table_version_and_the_clamp():
    assert method_inputs() == {
        "MAX_PLAUSIBLE_DELAY_SEC": db.MAX_PLAUSIBLE_DELAY_SEC,
        **{name: table.version for name, table in agg_registry.TABLES.items()},
    }


def test_a_table_bump_without_a_new_method_version_fails():
    live = method_inputs() | {"agg_route_daily": method_inputs()["agg_route_daily"] + 1}
    verdict = _verdict(METHOD_VERSIONS, live)
    assert verdict is not None and "agg_route_daily" in verdict and "METHOD_VERSIONS" in verdict


def test_a_clamp_change_without_a_new_method_version_fails(monkeypatch):
    monkeypatch.setattr(db, "MAX_PLAUSIBLE_DELAY_SEC", db.MAX_PLAUSIBLE_DELAY_SEC // 2)
    verdict = _verdict(METHOD_VERSIONS, method_inputs())
    assert verdict is not None and "MAX_PLAUSIBLE_DELAY_SEC" in verdict


def test_a_new_table_without_a_new_method_version_fails():
    assert "agg_new" in _verdict(METHOD_VERSIONS, method_inputs() | {"agg_new": 1})


def test_a_later_version_records_what_it_moved():
    base = MethodVersion("2026.11.0", {"MAX_PLAUSIBLE_DELAY_SEC": 7200, "agg_a": 1, "agg_b": 1})
    bumped = MethodVersion("2026.12.0", {"agg_a": 2})
    retired = MethodVersion("2027.1.0", {"agg_b": None})
    assert _verdict((base, bumped), {"MAX_PLAUSIBLE_DELAY_SEC": 7200, "agg_a": 2, "agg_b": 1}) is None
    assert _verdict((base, bumped, retired), {"MAX_PLAUSIBLE_DELAY_SEC": 7200, "agg_a": 2}) is None
    assert _verdict((base,), {"MAX_PLAUSIBLE_DELAY_SEC": 7200, "agg_a": 2, "agg_b": 1}) is not None


def test_method_version_is_the_newest_recorded_one():
    assert METHOD_VERSION == METHOD_VERSIONS[-1].version


@pytest.mark.parametrize("entry", METHOD_VERSIONS, ids=lambda entry: entry.version)
def test_a_version_reads_year_month_and_release(entry):
    assert _VERSION.match(entry.version), f"{entry.version} is not YYYY.M.N"


def test_versions_rise_in_order():
    keys = [_key(entry.version) for entry in METHOD_VERSIONS]
    assert keys == sorted(set(keys)), "METHOD_VERSIONS must list distinct versions, oldest first"


def test_every_later_version_changes_an_input():
    for i, entry in enumerate(METHOD_VERSIONS[1:], start=1):
        before = _fold(METHOD_VERSIONS[:i])
        assert entry.sets, f"{entry.version} records nothing"
        unchanged = [name for name, value in entry.sets.items() if before.get(name) == value]
        assert not unchanged, f"{entry.version} sets {', '.join(unchanged)} to the value it already had"


def test_the_doc_lists_every_method_version_in_order():
    assert _doc_versions() == [entry.version for entry in METHOD_VERSIONS], (
        f'{DOC.name}\'s "Method versions" table must have one row per METHOD_VERSIONS entry, oldest first'
    )
