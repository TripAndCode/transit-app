import pytest

from pipeline.reports.rankings import _weighted_avg_min


def test_weights_by_samples_not_mean_of_means():
    # 1.0 min over 1000 samples + 10.0 min over 10 samples.
    days = [
        {"avg_min": 1.0, "samples": 1000},
        {"avg_min": 10.0, "samples": 10},
    ]
    # Pooled = (1000*1 + 10*10)/1010 = 1.0891... NOT the (1+10)/2 = 5.5 mean-of-means.
    assert _weighted_avg_min(days) == pytest.approx((1000 * 1.0 + 10 * 10.0) / 1010, abs=1e-6)


def test_null_days_are_skipped_not_counted_as_zero():
    days = [
        {"avg_min": 4.0, "samples": 100},
        {"avg_min": None, "samples": 0},
    ]
    assert _weighted_avg_min(days) == pytest.approx(4.0)


def test_no_samples_returns_none():
    assert _weighted_avg_min([{"avg_min": None, "samples": 0}]) is None
    assert _weighted_avg_min([]) is None
