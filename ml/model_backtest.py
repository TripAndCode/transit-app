"""Rolling-origin evaluation of the models against B0 on the same cells. The
models are retrained once per week of origins, from data before that week only;
the origins within a week reuse them, each with features as of its own T−1.
Needs the optional `ml` dependency group."""

from __future__ import annotations

import copy
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import asdict, replace
from datetime import date, timedelta

import pandas as pd

from ml.backtest import AgencyResult, SliceRow, choose_origins
from ml.features import build_frame, route_ids, weekend_days, with_route_ids
from ml.metrics import ErrorStats
from ml.model_metrics import MODEL, add_cells, add_intervals, add_slot_intervals, cell_frame, take
from ml.model_result import MIN_MODEL_DAYS, SLOT_INTERVAL, IntervalStats, ModelBacktestResult
from ml.models import ModelParams, fit_calibrated, point_forecast, predict
from ml.training import HORIZON_DAYS, training_frame

RETRAIN_EVERY = 7


def run_backtest(
    runs: pd.DataFrame, *, params: ModelParams | None = None, origin_count: int = 28
) -> ModelBacktestResult:
    params = params or ModelParams()
    return run_backtests(runs, params=params, origin_count=origin_count, points=(params.point,))[params.point]


def run_backtests(
    runs: pd.DataFrame, *, params: ModelParams, origin_count: int = 28, points: Sequence[str]
) -> dict[str, ModelBacktestResult]:
    """One backtest of `params`' fits, scored once per point forecast in `points`
    ("mean", "q50"): each point's result is the one run_backtest gives for
    `params` with that point, without fitting the same models again."""
    runs = with_route_ids(runs, route_ids(runs))
    days = sorted({timestamp.date() for timestamp in runs["service_date"]})
    origins = choose_origins(days, origin_count, horizon=HORIZON_DAYS)
    rows: dict[str, dict[int, dict[tuple[str, int, bool, bool], SliceRow]]] = {
        point: defaultdict(dict) for point in points
    }
    intervals: dict[str, dict[str, IntervalStats]] = {
        MODEL: defaultdict(IntervalStats),
        SLOT_INTERVAL: defaultdict(IntervalStats),
        "B0": defaultdict(IntervalStats),
    }
    origin_skill: dict[str, list[tuple[date, float | None]]] = {point: [] for point in points}
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
            runs,
            cutoffs[-1],
            window_days=params.window_days,
            half_life_days=params.half_life_days,
            seed=params.seed,
            clip_quantile=params.clip_quantile,
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
            for skills in origin_skill.values():
                skills.extend((origin, None) for origin in week)
            continue
        age_days = (pd.Timestamp(cutoff) - train["service_date"]).dt.days
        train = train.assign(weight=(0.5 ** (age_days / params.half_life_days)).astype("float32"))
        models = fit_calibrated(train, params, cutoff, points=points)
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
                clip_quantile=params.clip_quantile,
            )
            if frame.empty:
                for skills in origin_skill.values():
                    skills.append((origin, None))
                continue
            preds = predict(models, frame)
            shared = {point: (ErrorStats(), ErrorStats()) for point in points}
            for agency_id, index in frame.groupby("agency_id").indices.items():
                sub, sub_preds = frame.iloc[index], take(preds, index)
                # Unlike add_cells below, not gated by on_model: this tracks the model's own
                # interval calibration wherever it was fit, independent of which agencies are
                # currently shown it, since the adoption gate's coverage check judges the
                # model's own calibration, not the current display routing (its skill checks
                # are deliberately routing-aware, through add_cells' own use_model gating).
                add_intervals(intervals[MODEL][str(agency_id)], intervals["B0"][str(agency_id)], sub, sub_preds)
                add_slot_intervals(intervals[SLOT_INTERVAL][str(agency_id)], sub, sub_preds)
                for point in points:
                    cells = cell_frame(sub, point_forecast(sub_preds, point))
                    m, b = add_cells(rows[point][int(agency_id)], cells, use_model=int(agency_id) in on_model)
                    shared[point][0].merge(m)
                    shared[point][1].merge(b)
            for point, (model_shared, b0_shared) in shared.items():
                skill = None
                if model_shared.mae is not None and b0_shared.mae:
                    skill = 1 - model_shared.mae / b0_shared.mae
                origin_skill[point].append((origin, skill))
    return {
        point: ModelBacktestResult(
            params=asdict(replace(params, point=point)),
            origins=origins,
            cutoffs=cutoffs,
            agencies=[
                _agency_result(runs, agency_id, rows[point][agency_id], origins, params)
                for agency_id in sorted(rows[point])
            ],
            intervals={method: copy.deepcopy(dict(by_agency)) for method, by_agency in intervals.items()},
            origin_skill=origin_skill[point],
            fallback_agencies=sorted(fallback & set(rows[point])),
        )
        for point in points
    }


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
