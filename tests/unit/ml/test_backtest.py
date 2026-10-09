from datetime import date, timedelta

import pytest

from ml.backtest import SPARSE_RUNS, choose_origins, evaluate_agency, results_from_json, results_to_json
from ml.cells import Cell

START = date(2026, 6, 1)


def _day(offset: int) -> date:
    return START + timedelta(days=offset)


def _daily(days: int, mean: float = 2.0, runs: int = 4, skip: tuple[int, ...] = ()) -> list[Cell]:
    return [Cell("R1", _day(d), 8, runs, mean * runs) for d in range(days) if d not in skip]


def test_origins_need_data_the_day_before_and_a_target_within_the_horizon():
    assert choose_origins([_day(d) for d in range(10)], count=3) == [_day(6), _day(7), _day(8)]
    assert choose_origins([_day(0), _day(5)], count=5) == [_day(1)]


def test_a_constant_series_is_predicted_exactly_by_every_baseline():
    result = evaluate_agency(8, _daily(70), origin_count=7)
    assert any(row.predicted_runs for row in result.rows)
    for row in result.rows:
        if row.predicted_runs:
            assert row.errors.mae == 0.0


def test_a_gap_day_is_neither_a_target_nor_a_zero_in_any_window():
    cells = _daily(70, skip=(66,))
    result = evaluate_agency(8, cells, origin_count=7)
    have = {c.service_date for c in cells}
    expected = sum(4 for o in result.origins for h in range(1, 8) if o + timedelta(days=h) in have)
    assert _day(67) not in result.origins
    assert sum(r.target_runs for r in result.rows if r.method == "B0") == expected
    # Origin 68's window spans the gap: a zero there would pull every baseline below 2.0.
    assert _day(68) in result.origins
    assert all(r.errors.mae in (0.0, None) for r in result.rows)


def test_a_route_with_no_history_counts_against_coverage_not_error():
    cells = [*_daily(70), Cell("NEW", _day(69), 8, 4, 40.0)]
    result = evaluate_agency(8, cells, origin_count=7)
    b0_rows = [r for r in result.rows if r.method == "B0"]
    assert sum(r.target_runs for r in b0_rows) > sum(r.predicted_runs for r in b0_rows)
    assert all(r.errors.mae in (0.0, None) for r in b0_rows)


def test_skill_is_measured_only_where_the_method_and_b0_both_predict():
    cells = [*_daily(70), Cell("R1", _day(69), 9, 4, 8.0)]
    result = evaluate_agency(8, cells, origin_count=7)
    # Hour 9 has no history, so B0 abstains there while B2 (route-wide) predicts.
    b2_rows = [r for r in result.rows if r.method == "B2"]
    assert sum(r.predicted_runs for r in b2_rows) > sum(r.paired.weight for r in b2_rows)
    for row in result.rows:
        assert row.paired.weight == row.paired_b0.weight


def test_cells_with_few_runs_are_sliced_apart():
    cells = [*_daily(70, runs=SPARSE_RUNS + 1), *(Cell("R2", _day(d), 17, 1, 1.0) for d in range(70))]
    result = evaluate_agency(8, cells, origin_count=7)
    assert {r.sparse for r in result.rows} == {True, False}
    assert all(r.peak for r in result.rows if r.sparse)


def test_an_agency_with_less_than_window_plus_a_week_is_flagged_and_still_runs():
    result = evaluate_agency(11, _daily(30), origin_count=28)
    assert result.short_history
    assert result.origins and any(r.predicted_runs for r in result.rows)


def test_results_survive_a_json_round_trip():
    result = evaluate_agency(8, _daily(40), origin_count=3)
    assert results_from_json(results_to_json([result])) == [result]


def test_an_agency_without_cells_is_refused_by_name():
    with pytest.raises(ValueError, match="agency 8"):
        evaluate_agency(8, [])
