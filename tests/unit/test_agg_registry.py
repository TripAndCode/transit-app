"""The aggregate registry names every table analyze builds, and only those."""

from pipeline import agg_registry, ch_aggregates
from pipeline.agg_registry import Kind
from pipeline.analyze import _AGG_TABLES_ORDERED, _STATIC_ONLY_AGG_TABLES

TIMETABLE_READERS = set(ch_aggregates.STATIC_JOINED_TABLES) | {
    "agg_route_headway",
    "agg_route_headway_daily",
    "agg_route_daily_dwell_run",
}


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


def test_exactly_the_timetable_readers_say_they_read_it():
    assert {n for n, t in agg_registry.TABLES.items() if t.reads_static} == TIMETABLE_READERS


def test_dependencies_name_registered_tables():
    for table in agg_registry.TABLES.values():
        assert set(table.depends_on) <= set(agg_registry.TABLES), table.name


def test_daily_headways_depend_on_the_scheduled_medians():
    assert agg_registry.TABLES["agg_route_headway_daily"].depends_on == ("agg_route_headway",)


def test_every_render_is_non_empty_text():
    for table in agg_registry.TABLES.values():
        parts = table.render()
        assert parts and all(isinstance(p, str) and p for p in parts), table.name
