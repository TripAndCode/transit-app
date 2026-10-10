"""LightGBM forecasters of a trip run's mean delay: one model for the mean and
one per quantile. Their interval is widened or narrowed on held-out days by
conformalized quantile regression. Needs the optional `ml` dependency group."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol

import lightgbm as lgb
import numpy as np
import pandas as pd

from ml.conformal import conformity_shift
from ml.features import CATEGORICAL, FEATURES, TARGET

QUANTILES = (0.1, 0.5, 0.9)
CALIBRATION_DAYS = 7
INTERVAL_COVERAGE = 0.8


@dataclass(frozen=True)
class ModelParams:
    window_days: int = 28
    half_life_days: float = 28.0
    num_leaves: int = 63
    learning_rate: float = 0.05
    rounds: int = 400
    min_data_in_leaf: int = 100
    feature_fraction: float = 0.9
    bagging_fraction: float = 0.8
    bagging_freq: int = 1
    lambda_l2: float = 1.0
    seed: int = 7

    def booster_params(self) -> dict[str, Any]:
        return {
            "num_leaves": self.num_leaves,
            "learning_rate": self.learning_rate,
            "min_data_in_leaf": self.min_data_in_leaf,
            "feature_fraction": self.feature_fraction,
            "bagging_fraction": self.bagging_fraction,
            "bagging_freq": self.bagging_freq,
            "lambda_l2": self.lambda_l2,
            "seed": self.seed,
            "verbose": -1,
        }


class Predictor(Protocol):
    def predict(self, data: pd.DataFrame) -> Any: ...


@dataclass
class ModelSet:
    mean: Predictor
    quantiles: dict[float, Predictor]
    interval_shift: float = 0.0


@dataclass(frozen=True)
class Predictions:
    mean: np.ndarray
    q10: np.ndarray
    q50: np.ndarray
    q90: np.ndarray


def fit(frame: pd.DataFrame, params: ModelParams) -> ModelSet:
    if frame.empty:
        raise ValueError("no training rows in this frame")
    data = lgb.Dataset(
        frame[FEATURES],
        label=frame[TARGET],
        weight=frame["weight"],
        categorical_feature=CATEGORICAL,
        free_raw_data=False,
    )
    base = params.booster_params()
    mean = lgb.train({**base, "objective": "regression"}, data, num_boost_round=params.rounds)
    quantiles: dict[float, Predictor] = {
        alpha: lgb.train({**base, "objective": "quantile", "alpha": alpha}, data, num_boost_round=params.rounds)
        for alpha in QUANTILES
    }
    return ModelSet(mean=mean, quantiles=quantiles)


def predict(models: ModelSet, frame: pd.DataFrame) -> Predictions:
    """Quantile models are trained apart and can cross; sorting each row puts
    them back in order. The calibration shift moves q10 and q90 outward (or
    inward) but never past q50."""
    data = frame[FEATURES]
    q = np.column_stack([np.asarray(models.quantiles[alpha].predict(data), dtype=float) for alpha in QUANTILES])
    q.sort(axis=1)
    q50 = q[:, 1]
    lo = np.minimum(q[:, 0] - models.interval_shift, q50)
    hi = np.maximum(q[:, 2] + models.interval_shift, q50)
    return Predictions(mean=np.asarray(models.mean.predict(data), dtype=float), q10=lo, q50=q50, q90=hi)


def split_calibration(frame: pd.DataFrame, cutoff: date) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The last CALIBRATION_DAYS of targets through the cutoff calibrate the
    interval; the rest train the models."""
    if frame.empty:
        return frame, frame
    start = pd.Timestamp(cutoff) - pd.Timedelta(days=CALIBRATION_DAYS)
    held = frame["service_date"] > start
    return frame[~held], frame[held]


def _min_rows_to_split(params: ModelParams) -> int:
    """A heuristic floor, not a derived guarantee: below it, a train set is
    certain to collapse to one constant prediction (confirmed empirically,
    not just by this formula — see test_models.py), because each boosting
    round samples only bagging_fraction of the rows before looking for a
    split (when bagging_freq > 0), on top of needing min_data_in_leaf twice
    over for two leaves. Above it, LightGBM usually can split, though not
    always at the smaller end of the range. Calling code that needs a
    guarantee of a non-degenerate model — not just a fit that avoids this
    specific collapse — must check its own history requirement; whether an
    agency has enough of its own history is the backtest orchestrator's own
    history-length check, not this function's."""
    floor = 2 * params.min_data_in_leaf
    if params.bagging_freq > 0:
        floor = math.ceil(floor / params.bagging_fraction)
    return floor


def fit_calibrated(frame: pd.DataFrame, params: ModelParams, cutoff: date) -> ModelSet:
    """Training on a thin split collapses to one constant prediction with no
    signal; train on the whole frame uncalibrated (interval_shift stays 0.0)
    below _min_rows_to_split instead, the same as when the split leaves
    nothing to train on at all. This widens the data the fit sees but does
    not guarantee a non-degenerate result — a frame that is itself too small
    collapses either way; see _min_rows_to_split."""
    train, calib = split_calibration(frame, cutoff)
    if len(train) < _min_rows_to_split(params):
        return fit(frame, params)
    models = fit(train, params)
    if not calib.empty:
        preds = predict(models, calib)
        y = calib[TARGET].to_numpy(dtype=float)
        models.interval_shift = conformity_shift(preds.q10, preds.q90, y, coverage=INTERVAL_COVERAGE)
    return models
