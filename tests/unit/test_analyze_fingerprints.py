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


def test_extra_dates_reach_a_dated_table_and_its_whole_history_table():
    ledger = ["2026-06-02"]
    current = table_fingerprints("static_join", True, ONE)
    extra = {"stop_route_days": ["2026-06-01"], "agg_stop_daily": ["2026-06-01"]}
    scopes, _ = agg_registry.table_scopes(current, dict(current), ledger, extra)
    assert scopes["agg_stop_daily"] == ["2026-06-01", "2026-06-02"]
    assert scopes["stop_route_days"] == ["2026-06-01", "2026-06-02"]
    assert scopes["agg_stop_routes"] is None
    assert scopes["agg_feed_health"] == ledger


TWO = ["static_trips:1:7", "static_stop_times:2:9"]


def test_a_loaded_fingerprint_names_the_content_it_was_built_against():
    from pipeline.analyze import _loaded_content_of
    from pipeline.static_archive import content_hash

    assert _loaded_content_of(table_fingerprints("static_join", True, TWO)["agg_route_headway"]) == content_hash(TWO)
    bumped = table_fingerprints("static_join", True, TWO, {"agg_route_headway": 9})["agg_route_headway"]
    assert _loaded_content_of(bumped) == content_hash(TWO)


def test_no_content_is_read_from_no_timetable_or_no_fingerprint():
    from pipeline.analyze import _loaded_content_of

    assert _loaded_content_of(table_fingerprints("static_join", False, [])["agg_route_headway"]) is None
    assert _loaded_content_of(None) is None
    assert _loaded_content_of("v1|clamp:7200|strategy:static_join") is None
