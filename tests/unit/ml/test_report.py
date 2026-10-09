from datetime import date, timedelta

import pytest

from ml.backtest import evaluate_agency
from ml.cells import Cell
from ml.report import render, summarize

START = date(2026, 6, 1)


def _results():
    days = [START + timedelta(days=d) for d in range(70)]
    steady = [Cell("R1", day, 8, 4, 8.0) for day in days]
    noisy = [Cell("R2", day, 17, 1, float(i % 5)) for i, day in enumerate(days)]
    short = [Cell("S1", day, 8, 3, 3.0) for day in days[:30]]
    return [evaluate_agency(8, steady + noisy, origin_count=7), evaluate_agency(11, short, origin_count=28)]


def test_b0_is_its_own_reference():
    assert summarize(_results(), "B0").skill_vs_b0 == 0.0


def test_skill_compares_against_b0_on_the_same_cells():
    b1 = summarize(_results(), "B1")
    assert b1.skill_vs_b0 is not None and b1.skill_vs_b0 < 0.0


def test_coverage_is_the_share_of_target_runs_a_method_predicts():
    coverage = summarize(_results(), "B0").coverage
    assert coverage is not None and 0.0 < coverage <= 1.0


@pytest.mark.parametrize("slice_", [{"peak": True}, {"sparse": True}, {"sparse": False}, {"horizon": 1}])
def test_slices_with_data_have_an_error(slice_):
    assert summarize(_results(), "B0", **slice_).mae is not None


def test_a_slice_without_data_has_none_rather_than_zero():
    summary = summarize(_results(), "B0", agency_id=999)
    assert summary.mae is None and summary.coverage is None and summary.skill_vs_b0 is None


def test_the_report_shows_every_method_every_slice_and_flags_short_history():
    html = render(_results(), generated=date(2026, 10, 9))
    assert html.startswith("<!doctype html>")
    for text in ("B0", "B1", "B2", "<svg", "Peak", "Off-peak", "≤ 2 runs", "Agency 11"):
        assert text in html
    assert html.count("short history") == 1
