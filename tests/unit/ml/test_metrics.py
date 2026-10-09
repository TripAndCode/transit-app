import pytest

from ml.metrics import ErrorStats


def test_errors_are_weighted_by_runs():
    stats = ErrorStats()
    stats.add(1.0, weight=3)
    stats.add(-3.0, weight=1)
    assert stats.mae == pytest.approx((3 * 1 + 3) / 4)
    assert stats.rmse == pytest.approx(((3 * 1 + 9) / 4) ** 0.5)


def test_no_weight_means_no_error_rather_than_zero():
    assert ErrorStats().mae is None and ErrorStats().rmse is None


def test_merging_adds_the_sums():
    a, b = ErrorStats(), ErrorStats()
    a.add(1.0, 1)
    b.add(3.0, 1)
    a.merge(b)
    assert a.mae == 2.0
