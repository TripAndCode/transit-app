"""The aggregate registry names every table analyze builds, and only those."""

from datetime import date

import pytest

from pipeline import agg_registry, ch_aggregates
from pipeline.agg_registry import Kind, Timetable
from pipeline.analyze import _AGG_TABLES_ORDERED, _STATIC_ONLY_AGG_TABLES

ARCHIVE_JOINED = set(ch_aggregates.STATIC_JOINED_TABLES) | {"agg_route_headway_daily", "agg_route_daily_dwell_run"}


def test_every_table_analyze_builds_is_registered_and_nothing_else():
    assert set(agg_registry.TABLES) == set(_AGG_TABLES_ORDERED) | set(ch_aggregates.INTERNAL_TABLES)


def test_each_entry_is_keyed_by_its_own_name():
    assert all(name == table.name for name, table in agg_registry.TABLES.items())


def test_versions_are_positive_integers():
    assert all(isinstance(t.version, int) and t.version >= 1 for t in agg_registry.TABLES.values())


def test_whole_history_tables_are_whole_and_depend_on_their_source():
    for name in ch_aggregates.WHOLE_HISTORY_TABLES:
        table = agg_registry.TABLES[name]
        assert table.kind is Kind.WHOLE
        assert table.depends_on == (ch_aggregates.whole_history_source(name),)


def test_static_only_tables_match_analyze():
    assert {n for n, t in agg_registry.TABLES.items() if t.kind is Kind.STATIC_ONLY} == set(_STATIC_ONLY_AGG_TABLES)


def test_only_agg_route_headway_is_built_from_the_loaded_timetable():
    assert {n for n, t in agg_registry.TABLES.items() if t.timetable is Timetable.LOADED} == {"agg_route_headway"}


def test_the_archive_joined_tables_are_the_stop_tables_dwell_and_the_daily_headways():
    assert {n for n, t in agg_registry.TABLES.items() if t.timetable is Timetable.ARCHIVED} == ARCHIVE_JOINED


def test_the_daily_headways_take_agg_route_headways_version_not_its_rows():
    table = agg_registry.TABLES["agg_route_headway_daily"]
    assert table.depends_on == ()
    assert table.versions_from == ("agg_route_headway",)


def test_dependencies_name_registered_tables():
    for table in agg_registry.TABLES.values():
        assert set(table.depends_on) <= set(agg_registry.TABLES), table.name


def test_a_version_override_reaches_a_table_built_in_the_loop(monkeypatch):
    monkeypatch.setitem(agg_registry._VERSIONS, "agg_route_stats", 2)
    assert {t.name: t for t in agg_registry._entries()}["agg_route_stats"].version == 2


def test_a_version_override_for_a_table_outside_the_loops_is_an_error(monkeypatch):
    monkeypatch.setitem(agg_registry._VERSIONS, "agg_feed_health", 2)
    with pytest.raises(ValueError, match="agg_feed_health"):
        agg_registry._entries()


def test_a_name_registered_twice_is_an_error():
    first = agg_registry.TABLES["agg_feed_health"]
    with pytest.raises(ValueError, match="agg_feed_health"):
        agg_registry._index([first, first])


def test_every_render_is_non_empty_text():
    for table in agg_registry.TABLES.values():
        parts = table.render()
        assert parts and all(isinstance(p, str) and p for p in parts), table.name


D = date(2026, 10, 1)


def _current() -> dict[str, str]:
    return {name: f"fp-{name}" for name in agg_registry.TABLES}


def test_without_a_ledger_every_table_is_rebuilt_in_full():
    scopes, reasons = agg_registry.table_scopes(_current(), _current(), None)
    assert set(scopes.values()) == {None}
    assert set(reasons.values()) == {"no ledger"}


def test_with_every_table_current_each_kind_takes_its_own_scope():
    scopes, reasons = agg_registry.table_scopes(_current(), _current(), [D])
    assert scopes["agg_route_daily"] == [D]
    assert scopes["agg_route_headway"] == []
    assert scopes["agg_route_stats"] is None and reasons["agg_route_stats"] == "source changed"


def test_nothing_changed_leaves_every_table_standing():
    scopes, reasons = agg_registry.table_scopes(_current(), _current(), [])
    assert all(scope == [] for scope in scopes.values())
    assert reasons == {}


def test_a_new_table_is_rebuilt_in_full():
    stored = _current()
    del stored["agg_hour_daily"]
    scopes, reasons = agg_registry.table_scopes(_current(), stored, [])
    assert scopes["agg_hour_daily"] is None and reasons["agg_hour_daily"] == "new"
    assert scopes["agg_route_daily"] == []


def test_a_changed_fingerprint_rebuilds_the_table_and_its_dependents():
    stored = _current() | {"route_delay_counts": "old"}
    scopes, reasons = agg_registry.table_scopes(_current(), stored, [])
    assert reasons["route_delay_counts"] == "fingerprint changed"
    for dependent in ("agg_route_stats", "agg_route_hour", "agg_route_hour_dow"):
        assert scopes[dependent] is None and reasons[dependent] == "depends on route_delay_counts"
    assert scopes["agg_route_daily"] == []


def test_the_scheduled_medians_rebuilding_leaves_the_daily_headways_to_their_dates():
    """The daily headways read the medians kept per timetable, not agg_route_headway's rows."""
    stored = _current() | {"agg_route_headway": "old"}
    scopes, _ = agg_registry.table_scopes(_current(), stored, [D])
    assert scopes["agg_route_headway"] is None
    assert scopes["agg_route_headway_daily"] == [D]
    assert scopes["agg_feed_health"] == [D]


def test_stop_route_days_carry_the_stop_routes():
    stored = _current() | {"stop_route_days": "old"}
    scopes, _ = agg_registry.table_scopes(_current(), stored, [])
    assert scopes["agg_stop_routes"] is None
