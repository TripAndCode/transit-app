"""Unit tests for the pure agg-freshness rule (no DB)."""

from datetime import date

from pipeline.freshness import is_stale


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
