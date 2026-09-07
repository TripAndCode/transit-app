"""Pure-logic tests for headway reconstruction and high-frequency classification.

No DB fixtures needed -- `pipeline.headways` is pure Python (see that module's
docstring for why the reconstruction/classification logic is split out from
its ClickHouse/Postgres callers in `pipeline.analyze`).
"""

from pipeline.headways import (
    HIGH_FREQUENCY_HEADWAY_SEC,
    headway_median_sec,
    is_high_frequency,
    reconstruct_headways,
)


def test_reconstruct_headways_low_noise_fixture_matches_hand_computed():
    # A route with vehicles at 08:00:00, 08:08:03, 08:16:01, 08:23:58
    # (seconds-since-midnight) -- a low-noise, close-to-8-minute-headway
    # fixture. Hand-computed gaps: 483s, 478s, 477s.
    times = [8 * 3600, 8 * 3600 + 8 * 60 + 3, 8 * 3600 + 16 * 60 + 1, 8 * 3600 + 23 * 60 + 58]
    gaps = reconstruct_headways(times)
    assert gaps == [483, 478, 477]
    # Within a few seconds of the hand-computed ~480s (8 min) median.
    assert abs(headway_median_sec(times) - 480) <= 6


def test_reconstruct_headways_sorts_unordered_input():
    # Order of input must not matter -- reconstruction sorts internally.
    times = [100, 0, 300, 190]
    assert reconstruct_headways(times) == [100, 90, 110]


def test_reconstruct_headways_needs_at_least_two_events():
    assert reconstruct_headways([]) == []
    assert reconstruct_headways([42]) == []
    assert headway_median_sec([]) is None
    assert headway_median_sec([42]) is None


def test_reconstruct_headways_duplicate_timestamp_is_a_zero_gap():
    assert reconstruct_headways([100, 100]) == [0]


def test_headway_median_sec_odd_and_even_gap_counts():
    # 3 events -> 2 gaps (10, 20) -> median of an even-length list.
    assert headway_median_sec([0, 10, 30]) == 15
    # 4 events -> 3 gaps (10, 10, 10) -> exact median.
    assert headway_median_sec([0, 10, 20, 30]) == 10


def test_is_high_frequency_known_8_minute_route():
    # ~8-minute scheduled headway median -> high-frequency.
    eight_min_sec = 8 * 60
    assert is_high_frequency(eight_min_sec) is True


def test_is_high_frequency_known_30_minute_route():
    # 30-minute scheduled headway median -> not high-frequency.
    thirty_min_sec = 30 * 60
    assert is_high_frequency(thirty_min_sec) is False


def test_is_high_frequency_boundary_and_missing_data():
    assert is_high_frequency(HIGH_FREQUENCY_HEADWAY_SEC) is True  # inclusive boundary
    assert is_high_frequency(HIGH_FREQUENCY_HEADWAY_SEC + 1) is False
    assert is_high_frequency(None) is False  # no schedule data is never high-frequency
