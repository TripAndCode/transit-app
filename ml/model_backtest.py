"""Rolling-origin evaluation of the models against B0 on the same cells. The
models are retrained once per week of origins, from data before that week only;
the origins within a week reuse them, each with features as of its own T−1.
Needs the optional `ml` dependency group."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict
from datetime import date, timedelta

import pandas as pd

from ml.backtest import AgencyResult, SliceRow, choose_origins
from ml.features import build_frame, route_ids, weekend_days, with_route_ids
from ml.metrics import ErrorStats
from ml.model_metrics import MODEL, add_cells, add_intervals, cell_frame, take
from ml.model_result import IntervalStats, ModelBacktestResult
from ml.models import ModelParams, fit_calibrated, predict
from ml.training import HORIZON_DAYS, training_frame

RETRAIN_EVERY = 7
# Below this many days of history before a cutoff, an agency keeps B0. A model
# trained mostly on other agencies forecasts one it has barely seen worse than
# that agency's own slot averages.
MIN_MODEL_DAYS = 56


def run_backtest(
    runs: pd.DataFrame, *, params: ModelParams | None = None, origin_count: int = 28
) -> ModelBacktestResult:
    params = params or ModelParams()
    runs = with_route_ids(runs, route_ids(runs))
    days = sorted({timestamp.date() for timestamp in runs["service_date"]})
    origins = choose_origins(days, origin_count, horizon=HORIZON_DAYS)
    rows: dict[int, dict[tuple[str, int, bool, bool], SliceRow]] = defaultdict(dict)
    intervals: dict[str, dict[str, IntervalStats]] = {
        MODEL: defaultdict(IntervalStats),
        "B0": defaultdict(IntervalStats),
    }
    origin_skill: list[tuple[date, float | None]] = []
    starts = list(range(0, len(origins), RETRAIN_EVERY))
    cutoffs = [origins[start] - timedelta(days=1) for start in starts]
    fallback: set[int] = set()
    all_agencies = {int(agency) for agency in runs["agency_id"].unique()}
    # training_targets' per-agency RNG draw for a given target day depends only on that
    # agency's first_day and the seed, never on the cutoff, and build_frame's own features
    # never read the cutoff either -- so one training_frame built at the LAST cutoff already
    # holds every earlier week's rows, unchanged, as a dated prefix. Each week takes that
    # prefix and reweights it from its own cutoff, instead of paying training_frame's full
    # per-origin cost again. See test_a_sliced_week_matches_training_frame_at_its_own_cutoff.
    full_frame = (
        training_frame(
            runs, cutoffs[-1], window_days=params.window_days, half_life_days=params.half_life_days, seed=params.seed
        )
        if cutoffs
        else pd.DataFrame()
    )
    days_table = weekend_days(runs)
    for start, cutoff in zip(starts, cutoffs, strict=True):
        week = origins[start : start + RETRAIN_EVERY]
        train = full_frame if full_frame.empty else full_frame[full_frame["service_date"] <= pd.Timestamp(cutoff)]
        if train.empty:
            fallback |= all_agencies
            origin_skill.extend((origin, None) for origin in week)
            continue
        age_days = (pd.Timestamp(cutoff) - train["service_date"]).dt.days
        train = train.assign(weight=(0.5 ** (age_days / params.half_life_days)).astype("float32"))
        models = fit_calibrated(train, params, cutoff)
        seen = runs[runs["service_date"] <= pd.Timestamp(cutoff)].groupby("agency_id")["service_date"].nunique()
        on_model = {int(agency) for agency, days in seen.items() if days >= MIN_MODEL_DAYS}
        fallback |= all_agencies - on_model
        for origin in week:
            frame = build_frame(
                runs,
                origin,
                window_days=params.window_days,
                horizon_days=HORIZON_DAYS,
                precomputed_weekend_days=days_table,
            )
            if frame.empty:
                origin_skill.append((origin, None))
                continue
            preds = predict(models, frame)
            model_shared, b0_shared = ErrorStats(), ErrorStats()
            for agency_id, index in frame.groupby("agency_id").indices.items():
                sub, sub_preds = frame.iloc[index], take(preds, index)
                # Unlike add_cells below, not gated by on_model: this tracks the model's own
                # interval calibration wherever it was fit, independent of which agencies are
                # currently shown it, since the adoption gate's coverage check judges the
                # model's own calibration, not the current display routing (its skill checks
                # are deliberately routing-aware, through add_cells' own use_model gating).
                add_intervals(intervals[MODEL][str(agency_id)], intervals["B0"][str(agency_id)], sub, sub_preds)
                m, b = add_cells(rows[int(agency_id)], cell_frame(sub, sub_preds), use_model=int(agency_id) in on_model)
                model_shared.merge(m)
                b0_shared.merge(b)
            skill = None
            if model_shared.mae is not None and b0_shared.mae:
                skill = 1 - model_shared.mae / b0_shared.mae
            origin_skill.append((origin, skill))
    return ModelBacktestResult(
        params=asdict(params),
        origins=origins,
        cutoffs=cutoffs,
        agencies=[_agency_result(runs, agency_id, rows[agency_id], origins, params) for agency_id in sorted(rows)],
        intervals={method: dict(by_agency) for method, by_agency in intervals.items()},
        origin_skill=origin_skill,
        fallback_agencies=sorted(fallback & set(rows)),
    )


def _agency_result(
    runs: pd.DataFrame,
    agency_id: int,
    rows: dict[tuple[str, int, bool, bool], SliceRow],
    origins: list[date],
    params: ModelParams,
) -> AgencyResult:
    dates = runs.loc[runs["agency_id"] == agency_id, "service_date"]
    days_of_data = dates.dt.normalize().nunique()
    return AgencyResult(
        agency_id=agency_id,
        first_day=dates.min().date(),
        last_day=dates.max().date(),
        days_of_data=int(days_of_data),
        origins=origins,
        short_history=days_of_data < params.window_days + HORIZON_DAYS,
        rows=[rows[key] for key in sorted(rows)],
    )
