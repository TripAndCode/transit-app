"""Fast, offline, always-run tests for `tests/fixtures/dashboard_value_check.py`
(item 22).

No DB, no browser, no network — this directory bypasses the DB fixtures per
CLAUDE.md's "pure logic tests under tests/unit/" convention (see
`tests/unit/conftest.py`). These exist so item 22's "verify a deliberately
wrong number makes the test fail, confirming it actually checks displayed
values and isn't a vacuous pass" requirement has a fast, always-runnable
proof, the same way item 23's `tests/unit/test_ask_eval_numeric_helper.py`
proves its own ground-truth comparison helper isn't vacuous without needing
a live LLM. The real end-to-end check
(`tests/dashboard_synthetic_display_test.py`) additionally needs a real
browser + the throwaway Postgres/ClickHouse stack and is gated off by
default — see that module's docstring for why, and for the one-time manual
corruption check a maintainer with a fully provisioned environment should
still run against the real rendered page before trusting this in CI.
"""

import pytest

from tests.fixtures.dashboard_value_check import (
    assert_avg_min_matches,
    assert_samples_matches,
    extract_leading_number,
)


def test_extract_leading_number_handles_unit_suffixes_and_grouping():
    assert extract_leading_number("0.9分") == 0.9
    assert extract_leading_number("0.9 min") == 0.9
    assert extract_leading_number("25") == 25.0
    assert extract_leading_number("1,234") == 1234.0


def test_extract_leading_number_raises_on_no_number():
    with pytest.raises(ValueError):
        extract_leading_number("—")


def test_assert_avg_min_matches_passes_for_correct_value():
    # 0.88 (item 21's outlier_spike expected avg_min, 2dp) rounds to 0.9 for
    # display via the frontend's own toFixed(1) — matches a "0.9分" cell.
    assert_avg_min_matches("0.9分", 0.88, label="outlier_spike")


def test_assert_avg_min_matches_fails_for_corrupted_expected_value():
    """The core anti-vacuous-pass check item 22 asks for: swapping in a wrong
    expected value must make the assertion fail, not silently pass."""
    with pytest.raises(AssertionError):
        assert_avg_min_matches("0.9分", 5.0, label="outlier_spike")


def test_assert_avg_min_matches_fails_for_wrong_displayed_cell():
    """Same anti-vacuous-pass check from the other direction: a correct
    expected value against a wrong (corrupted) displayed cell must also fail."""
    with pytest.raises(AssertionError):
        assert_avg_min_matches("5.0分", 0.88, label="outlier_spike")


def test_assert_samples_matches_passes_for_correct_value():
    assert_samples_matches("22", 22, label="null_delays")


def test_assert_samples_matches_fails_for_corrupted_value():
    with pytest.raises(AssertionError):
        assert_samples_matches("22", 25, label="null_delays")
