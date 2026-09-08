"""Pure-logic tests for scripts/probe_rt_field_coverage.py's threshold check.

No DB, no network -- `_assess` only compares an already-computed coverage
dict against the fixed thresholds tests/pipeline/test_static_join.py
confirms for agencies 8/9/10; the fetch/CLI plumbing needs real network and
isn't covered here.
"""

from __future__ import annotations

from scripts.probe_rt_field_coverage import _assess


def test_assess_empty_feed_has_no_stop_time_updates():
    result = _assess({"stop_time_updates": 0, "feed_timestamp": None})
    assert "no stop_time_updates" in result["assessment"]
    assert "matches_confirmed_agencies" not in result


def test_assess_matches_confirmed_agency_coverage():
    """Coverage shaped like the real hiroden/hirobus/hirokoh fixtures
    (tests/pipeline/test_static_join.py's own thresholds) must match on
    every field."""
    cov = {
        "stop_time_updates": 100,
        "feed_timestamp": 1_770_000_000,
        "stop_id_coverage": 1.0,
        "arr_delay_coverage": 0.2,
        "schedule_relationship_trip_coverage": 1.0,
        "schedule_relationship_stop_coverage": 1.0,
    }
    result = _assess(cov)
    assert all(result["matches_confirmed_agencies"].values())


def test_assess_flags_a_feed_that_never_sends_schedule_relationship():
    """A new agency whose feed never populates schedule_relationship_* must
    NOT be silently reported as matching -- this is exactly the gap item 103
    guards against before assuming service_delivered/dwell_run availability."""
    cov = {
        "stop_time_updates": 100,
        "feed_timestamp": 1_770_000_000,
        "stop_id_coverage": 1.0,
        "arr_delay_coverage": 0.2,
        "schedule_relationship_trip_coverage": 0.0,
        "schedule_relationship_stop_coverage": 0.0,
    }
    result = _assess(cov)
    checks = result["matches_confirmed_agencies"]
    assert checks["schedule_relationship_trip_coverage"] is False
    assert checks["schedule_relationship_stop_coverage"] is False
    assert checks["stop_id_coverage"] is True


def test_assess_flags_arr_delay_coverage_outside_sparse_range():
    """arr_delay must be genuinely sparse (item 87's finding) -- either
    always-present (1.0, suggesting a different field semantics) or
    always-absent (0.0) is flagged, not silently accepted."""
    cov = {
        "stop_time_updates": 100,
        "feed_timestamp": 1_770_000_000,
        "stop_id_coverage": 1.0,
        "arr_delay_coverage": 1.0,
        "schedule_relationship_trip_coverage": 1.0,
        "schedule_relationship_stop_coverage": 1.0,
    }
    result = _assess(cov)
    assert result["matches_confirmed_agencies"]["arr_delay_coverage"] is False


def test_assess_flags_implausible_feed_timestamp():
    cov = {
        "stop_time_updates": 100,
        "feed_timestamp": 1,  # plausible enum value, not a real epoch second
        "stop_id_coverage": 1.0,
        "arr_delay_coverage": 0.2,
        "schedule_relationship_trip_coverage": 1.0,
        "schedule_relationship_stop_coverage": 1.0,
    }
    result = _assess(cov)
    assert result["matches_confirmed_agencies"]["feed_timestamp"] is False
