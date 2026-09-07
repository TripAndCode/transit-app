"""Unit tests for the pure agg-freshness rule (no DB)."""

from datetime import date, datetime, timezone

from pipeline.freshness import feed_is_stale, is_stale


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


# ── feed_is_stale: (now - feed_timestamp), reusing is_stale's own rule ──────


def test_feed_none_is_not_stale():
    # No feed_timestamp at all (ingest strategy doesn't confirm it, or the
    # agency has no rows) is a structural gap, not evidence of an unhealthy
    # feed -- unlike is_stale's own agg_max_day=None branch.
    now = datetime(2026, 6, 15, 3, 0, 0, tzinfo=timezone.utc)
    assert feed_is_stale(now, None) is False


def test_feed_timestamp_same_jst_day_is_fresh():
    now = datetime(2026, 6, 15, 3, 0, 0, tzinfo=timezone.utc)  # 2026-06-15 12:00 JST
    feed_timestamp = datetime(2026, 6, 15, 2, 0, 0, tzinfo=timezone.utc)  # 2026-06-15 11:00 JST
    assert feed_is_stale(now, feed_timestamp) is False


def test_feed_timestamp_from_a_prior_jst_day_is_stale():
    now = datetime(2026, 6, 15, 3, 0, 0, tzinfo=timezone.utc)  # 2026-06-15 12:00 JST
    feed_timestamp = datetime(2026, 6, 13, 20, 0, 0, tzinfo=timezone.utc)  # 2026-06-14 05:00 JST
    assert feed_is_stale(now, feed_timestamp) is True
    # This must exactly match is_stale's own rule applied to the JST dates.
    assert feed_is_stale(now, feed_timestamp) == is_stale(date(2026, 6, 14), date(2026, 6, 15))


def test_feed_timestamp_ahead_of_now_is_fresh():
    # Clock skew putting the feed's own JST day slightly ahead of `now`'s
    # (here, just across a JST midnight boundary) must not be flagged
    # stale -- matches is_stale's "aggs ahead of the completed day" branch.
    now = datetime(2026, 6, 14, 14, 0, 0, tzinfo=timezone.utc)  # 2026-06-14 23:00 JST
    feed_timestamp = datetime(2026, 6, 14, 15, 30, 0, tzinfo=timezone.utc)  # 2026-06-15 00:30 JST
    assert feed_is_stale(now, feed_timestamp) is False
