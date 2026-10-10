from datetime import date

from tests.unit.ml.ml_group import require

pd = require("pandas")
np = require("numpy")
require("lightgbm")

from ml.model_metrics import add_cells, add_intervals, cell_frame, take  # noqa: E402
from ml.model_result import IntervalStats  # noqa: E402
from ml.models import Predictions  # noqa: E402


def _frame(slot_mean=(2.0, 2.0, np.nan)):
    return pd.DataFrame(
        {
            "agency_id": [8, 8, 8],
            "route_code": ["R1", "R1", "NEW"],
            "service_date": [pd.Timestamp(date(2026, 9, 9))] * 3,
            "hour": [8, 8, 8],
            "h": [1, 1, 1],
            "delay_min": [2.0, 4.0, 1.0],
            "slot_mean": list(slot_mean),
            "slot_p10": [1.0, 1.0, np.nan],
            "slot_p90": [3.0, 3.0, np.nan],
        }
    )


def _preds():
    return Predictions(
        mean=np.array([1.0, 3.0, 1.5]),
        q10=np.array([0.0, 0.0, 0.0]),
        q50=np.array([2.0, 2.0, 1.0]),
        q90=np.array([3.0, 3.0, 2.0]),
    )


def test_a_cell_pools_its_runs_predictions_and_carries_b0():
    cells = cell_frame(_frame(), _preds()).set_index("route_code")
    assert cells.loc["R1", "runs"] == 2
    assert cells.loc["R1", "actual"] == 3.0 and cells.loc["R1", "model"] == 2.0 and cells.loc["R1", "b0"] == 2.0
    assert np.isnan(cells.loc["NEW", "b0"])


def test_the_model_predicts_every_cell_and_skill_counts_only_shared_ones():
    rows = {}
    model_err, b0_err = add_cells(rows, cell_frame(_frame(), _preds()))
    predicted = {m: sum(r.predicted_runs for key, r in rows.items() if key[0] == m) for m in ("LGBM", "B0")}
    assert predicted == {"LGBM": 3, "B0": 2}
    assert model_err.weight == b0_err.weight == 2


def test_an_agency_kept_on_b0_scores_b0_wherever_b0_forecasts():
    rows = {}
    model_err, b0_err = add_cells(rows, cell_frame(_frame(), _preds()), use_model=False)
    assert model_err.abs_err == b0_err.abs_err
    predicted = {m: sum(r.predicted_runs for key, r in rows.items() if key[0] == m) for m in ("LGBM", "B0")}
    assert predicted == {"LGBM": 3, "B0": 2}


def test_take_selects_the_same_rows_across_every_field():
    sliced = take(_preds(), np.array([2, 0]))
    assert list(sliced.mean) == [1.5, 1.0] and list(sliced.q10) == [0.0, 0.0] and list(sliced.q90) == [2.0, 3.0]


def test_intervals_count_coverage_and_pinball_per_run():
    model, baseline = IntervalStats(), IntervalStats()
    add_intervals(model, baseline, _frame(), _preds())
    assert model.runs == 3 and model.covered == 2
    assert baseline.runs == 2 and baseline.covered == 1
    assert model.mean_pinball("0.5") is not None and baseline.mean_pinball("0.5") is None
