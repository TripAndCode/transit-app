"""Which analyze runs record the agency's headline before and after, read off
the plan's reasons, and that a headline that cannot be read costs only itself.

DB-free: the reasons come from ``agg_registry.table_scopes`` itself, and the
unreadable database is a port nothing listens on.
"""

import logging
from datetime import date

from pipeline import agg_registry
from pipeline.rebuild_headline import is_rebuild, read_before

D = date(2026, 10, 1)


def _current() -> dict[str, str]:
    return {name: f"fp-{name}" for name in agg_registry.TABLES}


def _reasons(stored: dict[str, str], ledger: list | None) -> dict[str, str]:
    _, reasons = agg_registry.table_scopes(_current(), stored, ledger)
    return reasons


def test_a_table_whose_method_version_moved_makes_a_rebuild():
    assert is_rebuild(_reasons(_current() | {"agg_route_daily_dist": "old"}, [D]))


def test_a_full_rebuild_is_one():
    """analyze --full deletes every stored fingerprint (mark_for_full_rebuild)."""
    assert is_rebuild(_reasons({}, []))


def test_a_run_with_no_ledger_to_scope_it_is_one():
    assert is_rebuild(_reasons(_current(), None))


def test_a_run_over_new_days_is_not_one():
    """Its whole-history tables are rebuilt in full too, but only to take in the
    changed days."""
    reasons = _reasons(_current(), [D])
    assert reasons and not is_rebuild(reasons)


def test_a_run_with_nothing_new_is_not_one():
    assert not is_rebuild(_reasons(_current(), []))


def test_a_headline_that_cannot_be_read_is_logged_and_skipped(caplog):
    with caplog.at_level(logging.WARNING, logger="pipeline.rebuild_headline"):
        assert read_before("postgresql://transit:transit@127.0.0.1:1/transit_test", 7) is None
    assert any("agency 7" in r.getMessage() for r in caplog.records)
