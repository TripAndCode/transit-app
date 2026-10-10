from datetime import date, timedelta

import pytest

from tests.unit.ml.ml_group import require

pd = require("pandas")
np = require("numpy")
require("lightgbm")

from ml.conformal import conformity_shift  # noqa: E402
from ml.features import FEATURES, build_frame, route_ids, with_route_ids  # noqa: E402
from ml.models import ModelParams, ModelSet, fit, fit_calibrated, predict, split_calibration  # noqa: E402
from ml.training import training_frame  # noqa: E402

START = date(2026, 6, 1)
FAST = ModelParams(rounds=60, num_leaves=7, min_data_in_leaf=5, half_life_days=28)


def _runs(days=70, seed=3):
    rng = np.random.default_rng(seed)
    rows = []
    for d in range(days):
        for route, level in (("LATE", 5.0), ("EARLY", 1.0)):
            for trip in range(3):
                rows.append(
                    {
                        "agency_id": 8,
                        "route_code": route,
                        "trip_id": f"{route}{trip}",
                        "service_date": pd.Timestamp(START + timedelta(days=d)),
                        "hour": 8 + trip,
                        "service": "wk",
                        "delay_min": level + rng.normal(0, 0.3),
                        "stops": 10,
                        "span_min": 30.0,
                    }
                )
    frame = pd.DataFrame(rows).astype(
        {
            "agency_id": "int16",
            "route_code": "string",
            "trip_id": "string",
            "hour": "int16",
            "service": "string",
            "delay_min": "float32",
        }
    )
    return with_route_ids(frame, route_ids(frame))


class _Const:
    def __init__(self, value):
        self.value = value

    def predict(self, data):
        return np.full(len(data), self.value, dtype=float)


def test_the_mean_model_learns_each_routes_level():
    runs = _runs()
    cutoff = START + timedelta(days=60)
    models = fit(training_frame(runs, cutoff, window_days=28, half_life_days=28), FAST)
    frame = build_frame(runs, cutoff + timedelta(days=1))
    by_route = frame.assign(pred=predict(models, frame).mean).groupby("route_code")["pred"].mean()
    assert abs(by_route["LATE"] - 5.0) < 0.5 and abs(by_route["EARLY"] - 1.0) < 0.5


def test_crossing_quantiles_are_put_in_order():
    models = ModelSet(mean=_Const(0.0), quantiles={0.1: _Const(5.0), 0.5: _Const(3.0), 0.9: _Const(1.0)})
    preds = predict(models, pd.DataFrame({name: [0.0] for name in FEATURES}))
    assert (preds.q10[0], preds.q50[0], preds.q90[0]) == (1.0, 3.0, 5.0)


def test_a_narrowing_shift_never_lifts_q10_above_the_median():
    models = ModelSet(
        mean=_Const(0.0), quantiles={0.1: _Const(1.0), 0.5: _Const(3.0), 0.9: _Const(5.0)}, interval_shift=-10.0
    )
    preds = predict(models, pd.DataFrame({name: [0.0] for name in FEATURES}))
    assert preds.q10[0] == preds.q50[0] == preds.q90[0] == 3.0


def test_the_last_week_before_the_cutoff_is_held_out_for_calibration():
    runs = _runs()
    cutoff = START + timedelta(days=60)
    train, calib = split_calibration(training_frame(runs, cutoff, window_days=28, half_life_days=28), cutoff)
    assert calib["service_date"].min() == pd.Timestamp(cutoff - timedelta(days=6))
    assert train["service_date"].max() == pd.Timestamp(cutoff - timedelta(days=7))


def test_calibration_brings_held_out_coverage_to_the_target():
    runs = _runs()
    cutoff = START + timedelta(days=60)
    frame = training_frame(runs, cutoff, window_days=28, half_life_days=28)
    models = fit_calibrated(frame, FAST, cutoff)
    _, calib = split_calibration(frame, cutoff)
    preds = predict(models, calib)
    y = calib["delay_min"].to_numpy()
    assert ((y >= preds.q10) & (y <= preds.q90)).mean() >= 0.8


def test_a_wider_coverage_never_shrinks_the_interval():
    rng = np.random.default_rng(1)
    y = rng.normal(0, 1, 500)
    lo, hi = np.full(500, -0.5), np.full(500, 0.5)
    shifts = [conformity_shift(lo, hi, y, coverage=c) for c in (0.5, 0.8, 0.95)]
    assert shifts == sorted(shifts)


def test_an_interval_already_too_wide_is_narrowed():
    y = np.zeros(100)
    assert conformity_shift(np.full(100, -5.0), np.full(100, 5.0), y, coverage=0.8) < 0


def test_fit_calibrated_trains_on_everything_when_all_rows_are_held_for_calibration():
    runs = _runs(days=20)
    cutoff = START + timedelta(days=14)
    frame = training_frame(runs, cutoff, window_days=28, half_life_days=28)
    train, calib = split_calibration(frame, cutoff)
    assert train.empty and not calib.empty  # the scenario this guards: a short-history agency

    models = fit_calibrated(frame, FAST, cutoff)
    assert models.interval_shift == 0.0
    preds = predict(models, frame)
    assert len(preds.mean) == len(frame)


def test_fit_calibrated_raises_a_clear_error_for_a_genuinely_empty_frame():
    with pytest.raises(ValueError, match="no training rows"):
        fit_calibrated(pd.DataFrame(), FAST, START)
