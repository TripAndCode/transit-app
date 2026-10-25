"""LightGBM forecasters of a trip run's delay: one model per quantile, plus a
mean model only when a requested point reads it. Their interval is widened or
narrowed on held-out days by conformalized quantile regression. Needs the
optional `ml` dependency group."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields
from datetime import date
from typing import Any, Protocol

import lightgbm as lgb
import numpy as np
import pandas as pd

from ml.conformal import conformity_shift
from ml.features import CATEGORICAL, FEATURES, TARGET, check_clip_quantile
from ml.model_result import DEFAULT_POINT, POINTS

QUANTILES = (0.1, 0.5, 0.9)
CALIBRATION_DAYS = 7
INTERVAL_COVERAGE = 0.8
MEAN_OBJECTIVES = ("regression", "huber")
# What a recorded params dict that predates these fields was fit and scored with.
_UNRECORDED = {"clip_quantile": None, "point": "mean", "mean_objective": "regression"}


@dataclass(frozen=True)
class ModelParams:
    """`clip_quantile` winsorizes the history the delay features are built from
    and the mean model's training target, per agency (see build_frame); the
    quantile models keep the raw target. `point` is the forecast a cell shows:
    the median model's by default, or the mean model's. A cell is scored by its
    absolute error, and the median does not follow one extreme run in a slot's
    history the way a squared-error mean does.

    `mean_objective` "huber" trains the mean model with a Huber loss whose
    `huber_delta` (minutes) is where a residual's loss turns from squared to
    linear. LightGBM starts Huber from the target's mean, so the delta must
    exceed most residuals' distance from that mean: once nearly every gradient
    is saturated, no split gains anything under lambda_l2 and the fit stops
    there, silently, with the mean as its only forecast."""

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
    clip_quantile: float | None = None
    point: str = DEFAULT_POINT
    mean_objective: str = "regression"
    huber_delta: float = 2.0

    def __post_init__(self) -> None:
        if self.point not in POINTS:
            raise ValueError(f"point must be one of {POINTS}, not {self.point!r}")
        if self.mean_objective not in MEAN_OBJECTIVES:
            raise ValueError(f"mean_objective must be one of {MEAN_OBJECTIVES}, not {self.mean_objective!r}")
        check_clip_quantile(self.clip_quantile)
        if not self.huber_delta > 0:
            raise ValueError(f"huber_delta must be positive, not {self.huber_delta}")

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> ModelParams:
        """The params a backtest result recorded (its `params`), so a model is
        fit with exactly what was judged. A record from before clip_quantile,
        point and mean_objective existed scored an unclipped squared-error
        mean. A record missing any other field, or naming one ModelParams does
        not have, does not say what was judged and is refused."""
        names = {field.name for field in fields(cls)}
        unknown = sorted(set(record) - names)
        missing = sorted(names - set(record) - set(_UNRECORDED) - {"huber_delta"})
        if unknown or missing:
            raise ValueError(f"a backtest's params must name every fit setting: missing {missing}, unknown {unknown}")
        return cls(**{**_UNRECORDED, **record})

    def mean_objective_params(self) -> dict[str, Any]:
        # LightGBM reads the Huber threshold from `alpha`; any other key is
        # ignored without a warning under verbose=-1.
        if self.mean_objective == "huber":
            return {"objective": "huber", "alpha": self.huber_delta}
        return {"objective": "regression"}

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
    """`mean` is None for a set fit without the mean model: one whose point is
    the median and that no caller asked to score the mean of."""

    mean: Predictor | None
    quantiles: dict[float, Predictor]
    interval_shift: float = 0.0


@dataclass(frozen=True)
class Predictions:
    mean: np.ndarray | None
    q10: np.ndarray
    q50: np.ndarray
    q90: np.ndarray


def fit(frame: pd.DataFrame, params: ModelParams, *, points: Sequence[str] | None = None) -> ModelSet:
    """`points` are the point forecasts the caller will read (params.point by
    default). The mean model is trained only when one of them is the mean."""
    if frame.empty:
        raise ValueError("no training rows in this frame")
    wanted = tuple(points or (params.point,))
    for point in wanted:
        _check_point(point)
    data = lgb.Dataset(
        frame[FEATURES],
        label=frame[TARGET],
        weight=frame["weight"],
        categorical_feature=CATEGORICAL,
        free_raw_data=False,
    )
    base = params.booster_params()
    quantiles: dict[float, Predictor] = {
        alpha: lgb.train({**base, "objective": "quantile", "alpha": alpha}, data, num_boost_round=params.rounds)
        for alpha in QUANTILES
    }
    mean: Predictor | None = None
    if "mean" in wanted:
        if params.clip_quantile is not None:
            data.set_label(_mean_target(frame, params))
        mean = lgb.train({**base, **params.mean_objective_params()}, data, num_boost_round=params.rounds)
    return ModelSet(mean=mean, quantiles=quantiles)


def _mean_target(frame: pd.DataFrame, params: ModelParams) -> pd.Series:
    """Each row's target clipped to its agency's bounds from that row's own
    origin window. A row whose agency had no history there has no bounds and
    keeps its target."""
    if not {"clip_lo", "clip_hi"} <= set(frame.columns):
        raise ValueError("clip_quantile needs a frame built with it: clip_lo/clip_hi are missing")
    return frame[TARGET].clip(lower=frame["clip_lo"], upper=frame["clip_hi"])


def _check_point(point: str) -> None:
    if point not in POINTS:
        raise ValueError(f"point must be one of {POINTS}, not {point!r}")


def point_forecast(preds: Predictions, point: str) -> np.ndarray:
    _check_point(point)
    if point == "q50":
        return preds.q50
    if preds.mean is None:
        raise ValueError("these predictions come from a model set fit without its mean model")
    return preds.mean


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
    mean = None if models.mean is None else np.asarray(models.mean.predict(data), dtype=float)
    return Predictions(mean=mean, q10=lo, q50=q50, q90=hi)


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


def fit_calibrated(
    frame: pd.DataFrame, params: ModelParams, cutoff: date, *, points: Sequence[str] | None = None
) -> ModelSet:
    """Training on a thin split collapses to one constant prediction with no
    signal; train on the whole frame uncalibrated (interval_shift stays 0.0)
    below _min_rows_to_split instead, the same as when the split leaves
    nothing to train on at all. This widens the data the fit sees but does
    not guarantee a non-degenerate result — a frame that is itself too small
    collapses either way; see _min_rows_to_split."""
    train, calib = split_calibration(frame, cutoff)
    if len(train) < _min_rows_to_split(params):
        return fit(frame, params, points=points)
    models = fit(train, params, points=points)
    if not calib.empty:
        preds = predict(models, calib)
        y = calib[TARGET].to_numpy(dtype=float)
        models.interval_shift = conformity_shift(preds.q10, preds.q90, y, coverage=INTERVAL_COVERAGE)
    return models
