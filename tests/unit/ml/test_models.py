from dataclasses import replace
from datetime import date, timedelta

import pytest

from tests.unit.ml.ml_group import require

pd = require("pandas")
np = require("numpy")
require("lightgbm")

from ml.conformal import conformity_shift  # noqa: E402
from ml.features import FEATURES, build_frame, route_ids, with_route_ids  # noqa: E402
from ml.models import (  # noqa: E402
    ModelParams,
    ModelSet,
    Predictions,
    fit,
    fit_calibrated,
    point_forecast,
    predict,
    split_calibration,
)
from ml.training import training_frame  # noqa: E402

START = date(2026, 6, 1)
FAST = ModelParams(rounds=60, num_leaves=7, min_data_in_leaf=5, half_life_days=28)
PRODUCTION_SCALE = ModelParams(rounds=60, num_leaves=7, min_data_in_leaf=100, half_life_days=28)


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


def test_the_last_week_through_the_cutoff_is_held_out_for_calibration():
    runs = _runs()
    cutoff = START + timedelta(days=60)
    train, calib = split_calibration(training_frame(runs, cutoff, window_days=28, half_life_days=28), cutoff)
    assert calib["service_date"].min() == pd.Timestamp(cutoff - timedelta(days=6))
    assert calib["service_date"].max() == pd.Timestamp(cutoff)
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


def test_fit_calibrated_falls_back_when_training_rows_are_too_few_to_split():
    runs = _runs(days=21)
    cutoff = START + timedelta(days=15)
    frame = training_frame(runs, cutoff, window_days=28, half_life_days=28)
    train, _ = split_calibration(frame, cutoff)
    assert 0 < len(train) < 2 * FAST.min_data_in_leaf  # too few rows to split even one leaf

    models = fit_calibrated(frame, FAST, cutoff)
    preds = predict(models, frame)
    by_route = frame.assign(pred=preds.mean).groupby("route_code")["pred"].mean()
    assert abs(by_route["LATE"] - by_route["EARLY"]) > 1.0  # not collapsed to one constant


def test_fit_calibrated_falls_back_at_production_scale_even_above_twice_min_data_in_leaf():
    """bagging_fraction (0.8 by default) samples only a share of train per round,
    so 2*min_data_in_leaf rows is not actually enough to split a leaf once
    bagging is in play; the guard must account for it, not just min_data_in_leaf."""
    runs = _runs(days=60)
    cutoff = START + timedelta(days=50)
    frame = training_frame(runs, cutoff, window_days=28, half_life_days=28)
    train, _ = split_calibration(frame, cutoff)
    # past 2x min_data_in_leaf, short of the bagging-adjusted floor
    assert 2 * PRODUCTION_SCALE.min_data_in_leaf <= len(train) < 230

    models = fit_calibrated(frame, PRODUCTION_SCALE, cutoff)
    preds = predict(models, frame)
    by_route = frame.assign(pred=preds.mean).groupby("route_code")["pred"].mean()
    assert abs(by_route["LATE"] - by_route["EARLY"]) > 1.0  # not collapsed to one constant


ROBUST = ModelParams(rounds=200, learning_rate=0.2, num_leaves=7, min_data_in_leaf=20, bagging_freq=0)


def _outlier_frame(n=400, seed=0):
    """Two slots, 1 and 5 minutes late, with one row in ten 100 minutes late
    and no feature that tells those rows apart. Each row's cap is its slot's
    level plus a minute."""
    rng = np.random.default_rng(seed)
    frame = pd.DataFrame({name: np.zeros(n, dtype="float32") for name in FEATURES})
    frame["agency_id"] = np.int16(8)
    frame["route_id"] = np.int32(0)
    level = np.where(np.arange(n) % 2 == 0, 1.0, 5.0)
    frame["slot_mean"] = level.astype("float32")
    y = level + rng.normal(0, 0.2, n)
    y[np.arange(n) % 20 < 2] = 100.0
    return frame.assign(delay_min=y.astype("float32"), weight=np.float32(1.0), clip_lo=0.0, clip_hi=level + 1.0)


def _mean_levels(params):
    frame = _outlier_frame()
    return frame.assign(pred=predict(fit(frame, params), frame).mean).groupby("slot_mean")["pred"].mean()


def test_huber_keeps_a_few_extreme_targets_from_pulling_the_mean_model():
    squared = _mean_levels(ROBUST)
    assert squared[1.0] > 8.0 and squared[5.0] > 12.0
    # LightGBM starts Huber from the mean, 12.7 here, so a delta below ~12
    # saturates every gradient, no split gains anything, and the fit stops there.
    huber = _mean_levels(replace(ROBUST, mean_objective="huber", huber_delta=15.0))
    assert huber[1.0] < 3.5 and huber[5.0] < 7.5
    loose = _mean_levels(replace(ROBUST, mean_objective="huber", huber_delta=1e6))
    np.testing.assert_allclose(loose, squared, atol=0.5)


def test_clipping_bounds_the_mean_models_target_but_not_the_quantile_models():
    frame = _outlier_frame()
    raw, clipped = predict(fit(frame, ROBUST), frame), predict(fit(frame, replace(ROBUST, clip_quantile=0.99)), frame)
    assert (clipped.mean <= frame["clip_hi"].to_numpy() + 1e-6).all() and raw.mean.mean() > 8.0
    for name in ("q10", "q50", "q90"):
        np.testing.assert_allclose(getattr(clipped, name), getattr(raw, name))


def test_a_run_whose_agency_had_no_cap_keeps_its_target_unclipped():
    frame = _outlier_frame().assign(clip_lo=np.nan, clip_hi=np.nan)
    raw, clipped = predict(fit(frame, ROBUST), frame), predict(fit(frame, replace(ROBUST, clip_quantile=0.99)), frame)
    np.testing.assert_allclose(clipped.mean, raw.mean)


def test_clipping_needs_a_frame_built_with_its_caps():
    with pytest.raises(ValueError, match="clip_lo"):
        fit(_outlier_frame().drop(columns=["clip_lo", "clip_hi"]), replace(ROBUST, clip_quantile=0.99))


def test_the_point_forecast_is_the_mean_or_the_median():
    preds = Predictions(mean=np.array([3.0]), q10=np.array([0.0]), q50=np.array([1.0]), q90=np.array([2.0]))
    assert point_forecast(preds, "mean")[0] == 3.0
    assert point_forecast(preds, "q50")[0] == 1.0


@pytest.mark.parametrize(
    "overrides", [{"point": "q90"}, {"mean_objective": "l1"}, {"clip_quantile": 0.3}, {"huber_delta": 0.0}]
)
def test_model_params_refuse_an_option_they_cannot_honour(overrides):
    with pytest.raises(ValueError):
        ModelParams(**overrides)


def test_params_rebuild_exactly_from_the_record_a_backtest_keeps():
    from dataclasses import asdict

    params = ModelParams(window_days=21, clip_quantile=0.99, point="mean", mean_objective="huber", huber_delta=3.0)
    assert ModelParams.from_record(asdict(params)) == params


def test_a_record_from_before_the_point_existed_reads_as_an_unclipped_squared_error_mean():
    from dataclasses import asdict

    legacy = asdict(ModelParams(window_days=21))
    for name in ("clip_quantile", "point", "mean_objective", "huber_delta"):
        del legacy[name]
    params = ModelParams.from_record(legacy)
    assert (params.window_days, params.point, params.clip_quantile, params.mean_objective) == (
        21,
        "mean",
        None,
        "regression",
    )


@pytest.mark.parametrize("change", [{"window_days": ...}, {"max_depth": 5}])
def test_a_record_that_is_missing_or_adds_a_fit_setting_is_refused(change):
    from dataclasses import asdict

    record = {**asdict(ModelParams()), **change}
    record = {key: value for key, value in record.items() if value is not ...}
    with pytest.raises(ValueError):
        ModelParams.from_record(record)
