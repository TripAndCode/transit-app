"""Scoring a model's run predictions. Intervals are scored per run, the unit the
quantile models predict. Point error is scored on the route×date×hour cells the
baselines are scored on, weighted by runs. Needs the optional `ml` dependency group."""

from __future__ import annotations

import numpy as np
import pandas as pd

from ml.backtest import PEAK_HOURS, SPARSE_RUNS, SliceRow
from ml.metrics import ErrorStats
from ml.model_result import IntervalStats
from ml.models import Predictions

MODEL = "LGBM"
CELL = ["agency_id", "route_code", "service_date", "hour", "h"]


def take(preds: Predictions, index: np.ndarray) -> Predictions:
    return Predictions(mean=preds.mean[index], q10=preds.q10[index], q50=preds.q50[index], q90=preds.q90[index])


def add_intervals(model: IntervalStats, baseline: IntervalStats, frame: pd.DataFrame, preds: Predictions) -> None:
    y = frame["delay_min"].to_numpy(dtype=float)
    _add(model, y, preds.q10, preds.q90, {"0.1": preds.q10, "0.5": preds.q50, "0.9": preds.q90})
    has_b0 = frame["slot_p10"].notna().to_numpy() & frame["slot_p90"].notna().to_numpy()
    p10 = frame["slot_p10"].to_numpy(dtype=float)[has_b0]
    p90 = frame["slot_p90"].to_numpy(dtype=float)[has_b0]
    _add(baseline, y[has_b0], p10, p90, {"0.1": p10, "0.9": p90})


def _add(stats: IntervalStats, y: np.ndarray, lo: np.ndarray, hi: np.ndarray, quantiles: dict[str, np.ndarray]) -> None:
    stats.runs += len(y)
    stats.covered += int(((y >= lo) & (y <= hi)).sum())
    stats.width += float((hi - lo).sum())
    for alpha, q in quantiles.items():
        a, diff = float(alpha), y - q
        stats.pinball[alpha] = stats.pinball.get(alpha, 0.0) + float(np.maximum(a * diff, (a - 1) * diff).sum())


def cell_frame(frame: pd.DataFrame, preds: Predictions) -> pd.DataFrame:
    """Every run in a cell shares its slot, so the cell's B0 is any run's slot mean."""
    scored = frame[[*CELL, "delay_min", "slot_mean"]].assign(model=preds.mean)
    cells = scored.groupby(CELL, observed=True).agg(
        runs=("delay_min", "size"),
        actual=("delay_min", "mean"),
        model=("model", "mean"),
        b0=("slot_mean", "first"),
    )
    return cells.reset_index()


def add_cells(
    rows: dict[tuple[str, int, bool, bool], SliceRow], cells: pd.DataFrame, *, use_model: bool = True
) -> tuple[ErrorStats, ErrorStats]:
    """Scores B0 and the forecast the app would show: the model's, or B0's where
    `use_model` is off (an agency with too little history), the model's only
    where B0 has no history either."""
    model_shared, b0_shared = ErrorStats(), ErrorStats()
    for cell in cells.itertuples(index=False):
        runs, actual = int(cell.runs), float(cell.actual)
        key = (int(cell.h), int(cell.hour) in PEAK_HOURS, runs <= SPARSE_RUNS)
        b0 = None if pd.isna(cell.b0) else float(cell.b0)
        shown = float(cell.model) if use_model or b0 is None else b0
        for method, prediction in (("B0", b0), (MODEL, shown)):
            row = rows.setdefault((method, *key), SliceRow(method, *key))
            row.target_runs += runs
            if prediction is None:
                continue
            row.predicted_runs += runs
            row.errors.add(prediction - actual, runs)
            if b0 is not None:
                row.paired.add(prediction - actual, runs)
                row.paired_b0.add(b0 - actual, runs)
        if b0 is not None:
            model_shared.add(shown - actual, runs)
            b0_shared.add(b0 - actual, runs)
    return model_shared, b0_shared
