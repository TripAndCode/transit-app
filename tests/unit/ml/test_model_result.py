from datetime import date

from ml.backtest import AgencyResult, SliceRow
from ml.metrics import ErrorStats
from ml.model_result import IntervalStats, ModelBacktestResult, merged_intervals, result_from_json, result_to_json


def _stats(runs, covered, width):
    return IntervalStats(runs=runs, covered=covered, width=width, pinball={"0.1": 1.0, "0.9": 2.0})


def _result():
    row = SliceRow("LGBM", 1, True, False, target_runs=4, predicted_runs=4, errors=ErrorStats(4.0, 2.0, 1.0))
    agency = AgencyResult(8, date(2026, 6, 1), date(2026, 9, 1), 90, [date(2026, 8, 20)], False, [row])
    return ModelBacktestResult(
        params={"window_days": 28},
        origins=[date(2026, 8, 20)],
        cutoffs=[date(2026, 8, 19)],
        agencies=[agency],
        intervals={"LGBM": {"8": _stats(10, 8, 30.0), "9": _stats(10, 9, 10.0)}},
        origin_skill=[(date(2026, 8, 20), 0.07)],
    )


def test_interval_stats_report_coverage_width_and_pinball_per_run():
    stats = _stats(10, 8, 30.0)
    assert stats.coverage == 0.8 and stats.mean_width == 3.0 and stats.mean_pinball("0.9") == 0.2
    assert IntervalStats().coverage is None and IntervalStats().mean_pinball("0.5") is None


def test_agencies_merge_into_one_interval_figure():
    merged = merged_intervals(_result(), "LGBM")
    assert merged.runs == 20 and merged.covered == 17 and merged.coverage == 0.85


def test_a_result_survives_a_json_round_trip():
    result = _result()
    assert result_from_json(result_to_json(result)) == result
