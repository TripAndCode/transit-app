"""Unit tests for the agg-freshness rule and the verdict built on it (no DB)."""

from datetime import date

import pipeline.freshness
from pipeline.freshness import StaleAgency, is_stale, stale_verdict


def test_no_completed_days_is_fresh():
    # Agency has no civil day strictly before today → nothing is owed.
    assert is_stale(None, None) is False
    assert is_stale(date(2026, 6, 10), None) is False


def test_empty_aggs_with_completed_day_is_stale():
    # A completed day exists but the agency was never analyzed.
    assert is_stale(None, date(2026, 6, 15)) is True


def test_aggs_behind_completed_day_is_stale():
    assert is_stale(date(2026, 6, 14), date(2026, 6, 15)) is True


def test_aggs_cover_completed_day_is_fresh():
    assert is_stale(date(2026, 6, 15), date(2026, 6, 15)) is False


def test_aggs_ahead_of_completed_day_is_fresh():
    assert is_stale(date(2026, 6, 16), date(2026, 6, 15)) is False


class _Conn:
    def __init__(self):
        self.rolled_back = False

    def rollback(self):
        self.rolled_back = True


def _check_returning(*stale):
    return lambda _conn, _ch, _agency_ids: list(stale)


def test_the_verdict_on_aggregates_behind_names_both_days(monkeypatch):
    monkeypatch.setattr(
        pipeline.freshness,
        "check_agg_freshness",
        _check_returning(StaleAgency(3, date(2026, 6, 14), date(2026, 6, 15))),
    )
    assert stale_verdict(_Conn(), None, 3) == (
        "aggregates still stale: they end at 2026-06-14, the newest completed day is 2026-06-15"
    )


def test_the_verdict_on_an_agency_never_analyzed_says_none_are_built(monkeypatch):
    monkeypatch.setattr(
        pipeline.freshness, "check_agg_freshness", _check_returning(StaleAgency(3, None, date(2026, 6, 15)))
    )
    assert stale_verdict(_Conn(), None, 3) == (
        "aggregates still stale: none are built yet, the newest completed day is 2026-06-15"
    )


def test_fresh_aggregates_have_no_verdict(monkeypatch):
    monkeypatch.setattr(pipeline.freshness, "check_agg_freshness", _check_returning())
    assert stale_verdict(_Conn(), None, 3) is None


def test_a_check_that_fails_has_no_verdict_and_leaves_the_connection_usable(monkeypatch):
    def _boom(*_a):
        raise OSError("clickhouse went away")

    monkeypatch.setattr(pipeline.freshness, "check_agg_freshness", _boom)
    conn = _Conn()
    assert stale_verdict(conn, None, 3) is None
    assert conn.rolled_back
