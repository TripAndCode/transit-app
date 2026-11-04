"""Each table's fingerprint carries the timetable only where the table reads it."""

import dataclasses

from pipeline import agg_registry
from pipeline.agg_registry import Timetable
from pipeline.analyze import table_fingerprints

ONE = ["static_trips:1:7"]
OTHER = ["static_trips:1:8"]


def _of(kind: Timetable) -> set[str]:
    return {name for name, table in agg_registry.TABLES.items() if table.timetable is kind}


def _moved(before: dict[str, str], after: dict[str, str]) -> set[str]:
    return {name for name in before if before[name] != after[name]}


def test_a_stamping_agencys_new_timetable_moves_only_what_the_loaded_one_builds():
    moved = _moved(table_fingerprints("static_join", True, ONE), table_fingerprints("static_join", True, OTHER))
    assert moved == _of(Timetable.LOADED)


def test_a_non_stamping_agencys_new_timetable_moves_every_timetable_reader():
    for strategy in (None, "aomori_regex"):
        moved = _moved(table_fingerprints(strategy, True, ONE), table_fingerprints(strategy, True, OTHER))
        assert moved == _of(Timetable.LOADED) | _of(Timetable.ARCHIVED), strategy


def test_gaining_a_timetable_moves_the_archive_joined_tables_too():
    moved = _moved(table_fingerprints("static_join", False, []), table_fingerprints("static_join", True, ONE))
    assert moved == _of(Timetable.LOADED) | _of(Timetable.ARCHIVED)


def test_the_daily_headways_carry_agg_route_headways_version(monkeypatch):
    before = table_fingerprints("static_join", True, ONE)
    table = agg_registry.TABLES["agg_route_headway"]
    monkeypatch.setitem(agg_registry.TABLES, "agg_route_headway", dataclasses.replace(table, version=table.version + 1))
    after = table_fingerprints("static_join", True, ONE)
    assert _moved(before, after) == {"agg_route_headway", "agg_route_headway_daily"}


def test_given_versions_fold_their_own_source_version():
    versions = dict.fromkeys(agg_registry.TABLES, 1)
    bumped = {**versions, "agg_route_headway": 2}
    moved = _moved(
        table_fingerprints("static_join", True, ONE, versions), table_fingerprints("static_join", True, ONE, bumped)
    )
    assert moved == {"agg_route_headway", "agg_route_headway_daily"}


def test_a_new_loaded_timetable_does_not_cascade_to_the_daily_headways():
    ledger = ["2026-06-01"]
    current = table_fingerprints("static_join", True, OTHER)
    stored = {**current, "agg_route_headway": table_fingerprints("static_join", True, ONE)["agg_route_headway"]}
    scopes, _ = agg_registry.table_scopes(current, stored, ledger)
    assert scopes["agg_route_headway"] is None
    assert scopes["agg_route_headway_daily"] == ledger
