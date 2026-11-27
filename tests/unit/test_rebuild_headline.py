"""Which analyze runs record the agency's headline before and after, read off
the plan's reasons, and that a headline that cannot be read costs only itself.

DB-free: the reasons come from ``agg_registry.table_scopes`` itself, and the
unreadable database is a port nothing listens on.
"""

import asyncio
import logging
import time
from datetime import date

from pipeline import agg_registry, rebuild_headline
from pipeline.rebuild_headline import Before, is_rebuild, read_before

D = date(2026, 10, 1)


def _current() -> dict[str, str]:
    return {name: f"fp-{name}" for name in agg_registry.TABLES}


def _reasons(stored: dict[str, str], ledger: list | None) -> dict[str, str]:
    _, reasons = agg_registry.table_scopes(_current(), stored, ledger)
    return reasons


def test_a_table_whose_method_version_moved_makes_a_rebuild():
    assert is_rebuild(_reasons(_current() | {"agg_route_daily_dist": "old"}, [D]))


def test_a_bump_to_a_table_the_headline_does_not_read_is_not_one():
    """agg_route_daily_dist stands, so its figures before and after are the same rows."""
    reasons = _reasons(_current() | {"agg_feed_health": "old"}, [D])
    assert reasons["agg_feed_health"] == "fingerprint changed"
    assert not is_rebuild(reasons)


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


class _SlowConn:
    async def fetchrow(self, *_args):
        await asyncio.sleep(3)
        return {"first_day": None, "last_day": None}

    async def close(self):
        pass


def test_a_side_that_runs_past_its_bound_is_given_up_on(monkeypatch, caplog):
    """The run waits on the read with its own transaction open, so the bound
    covers the whole side, not each statement in it."""

    async def _connect(*_args, **_kwargs):
        return _SlowConn()

    monkeypatch.setattr(rebuild_headline.asyncpg, "connect", _connect)
    monkeypatch.setattr(rebuild_headline, "_READ_TIMEOUT_SEC", 0.2)
    started = time.monotonic()
    with caplog.at_level(logging.WARNING, logger="pipeline.rebuild_headline"):
        assert read_before("postgresql://transit:secret@db/transit", 7) is None
    assert time.monotonic() - started < 2
    assert any("agency 7" in r.getMessage() for r in caplog.records)


def test_the_database_url_stays_out_of_a_before_s_repr():
    """It carries the password, and a repr is what a log line or a traceback prints."""
    url = "postgresql://transit:secret@db/transit"
    before = Before(url, 7, D, D, (60, 300), "standard_m1_p5", {"on_time_pct": 90.0})
    assert url not in repr(before) and "secret" not in repr(before)
