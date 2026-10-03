"""Pure unit tests for the movers comparison windows (DB-free)."""

from datetime import date, timedelta

import pytest

from pipeline.dashboard_queries import movers_windows


def test_current_window_ends_on_to_date_and_is_window_days_long():
    (cur_from, cur_to), _prv = movers_windows(date(2026, 4, 14), 7)
    assert cur_to == date(2026, 4, 14)
    assert cur_from == date(2026, 4, 8)
    assert (cur_to - cur_from).days + 1 == 7


def test_prior_window_is_adjacent_and_never_overlaps():
    (cur_from, _cur_to), (prv_from, prv_to) = movers_windows(date(2026, 4, 14), 7)
    assert prv_to == cur_from - timedelta(days=1)
    assert prv_from == date(2026, 4, 1)
    assert prv_to < cur_from


@pytest.mark.parametrize("window_days", [1, 2, 7, 14, 30])
def test_both_windows_have_equal_length_and_stay_disjoint(window_days):
    (cur_from, cur_to), (prv_from, prv_to) = movers_windows(date(2026, 6, 30), window_days)
    assert (cur_to - cur_from).days + 1 == window_days
    assert (prv_to - prv_from).days + 1 == window_days
    assert prv_to < cur_from


def test_single_day_window():
    (cur_from, cur_to), (prv_from, prv_to) = movers_windows(date(2026, 1, 1), 1)
    assert cur_from == cur_to == date(2026, 1, 1)
    assert prv_from == prv_to == date(2025, 12, 31)


def test_rejects_non_positive_window():
    with pytest.raises(ValueError):
        movers_windows(date(2026, 1, 1), 0)
