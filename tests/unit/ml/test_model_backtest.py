from dataclasses import replace
from datetime import date, timedelta

import pytest

from tests.unit.ml.ml_group import require

pd = require("pandas")
np = require("numpy")
require("lightgbm")

from ml.features import route_ids, with_route_ids  # noqa: E402
from ml.model_backtest import run_backtest, run_backtests  # noqa: E402
from ml.model_result import MODEL, SLOT_INTERVAL, result_from_json, result_to_json  # noqa: E402
from ml.models import ModelParams  # noqa: E402
from ml.training import training_frame  # noqa: E402

START = date(2026, 6, 1)
FAST = ModelParams(rounds=40, num_leaves=7, min_data_in_leaf=5)


def _runs(days=80):
    rng = np.random.default_rng(5)
    rows = [
        {
            "agency_id": agency,
            "route_code": route,
            "trip_id": f"{route}{trip}",
            "service_date": pd.Timestamp(START + timedelta(days=d)),
            "hour": 7 + trip,
            "service": "wk",
            "delay_min": base + (1.5 if (START + timedelta(days=d)).weekday() >= 5 else 0) + rng.normal(0, 0.5),
            "stops": 10,
            "span_min": 30.0,
        }
        for d in range(days)
        for agency, route, base in ((8, "R1", 3.0), (8, "R2", 1.0), (9, "B1", 2.0))
        for trip in range(2)
    ]
    return pd.DataFrame(rows).astype(
        {
            "agency_id": "int16",
            "route_code": "string",
            "trip_id": "string",
            "hour": "int16",
            "service": "string",
            "delay_min": "float32",
            "stops": "int16",
            "span_min": "float32",
        }
    )


def test_each_week_of_origins_trains_on_data_before_it():
    result = run_backtest(_runs(), params=FAST, origin_count=10)
    assert len(result.cutoffs) == 2
    assert all(cutoff < origin for cutoff, origin in zip(result.cutoffs, result.origins[::7], strict=True))


def test_b0_and_the_model_are_scored_on_the_same_target_cells():
    result = run_backtest(_runs(), params=FAST, origin_count=7)
    for agency in result.agencies:
        b0 = sum(r.target_runs for r in agency.rows if r.method == "B0")
        model = sum(r.target_runs for r in agency.rows if r.method == "LGBM")
        assert b0 == model > 0
    assert {"LGBM", "B0"} <= set(result.intervals)


def test_an_agency_with_too_little_history_keeps_b0():
    runs = _runs()
    late = (runs["agency_id"] == 9) & (runs["service_date"] < pd.Timestamp(START + timedelta(days=30)))
    result = run_backtest(runs[~late].reset_index(drop=True), params=FAST, origin_count=7)
    assert result.fallback_agencies == [9]
    short = next(a for a in result.agencies if a.agency_id == 9)
    model = sum(r.paired.abs_err for r in short.rows if r.method == "LGBM")
    b0 = sum(r.paired_b0.abs_err for r in short.rows if r.method == "LGBM")
    assert model == b0


def test_a_result_round_trips_through_json():
    result = run_backtest(_runs(), params=FAST, origin_count=7)
    assert result_from_json(result_to_json(result)) == result


def test_a_retrain_week_too_thin_to_train_on_is_skipped_not_a_crash():
    result = run_backtest(_runs(days=8), params=FAST, origin_count=28)
    assert result.origin_skill
    assert all(skill is None for _, skill in result.origin_skill)


def test_fallback_agencies_only_names_agencies_the_report_can_show():
    runs = _runs()
    extra = pd.DataFrame(
        [
            {
                "agency_id": 50,
                "route_code": "X1",
                "trip_id": "X10",
                "service_date": pd.Timestamp(START),
                "hour": 7,
                "service": "wk",
                "delay_min": 1.0,
                "stops": 10,
                "span_min": 30.0,
            }
        ]
    ).astype(runs.dtypes.to_dict())
    result = run_backtest(pd.concat([runs, extra], ignore_index=True), params=FAST, origin_count=7)
    assert 50 not in {agency.agency_id for agency in result.agencies}
    assert 50 not in result.fallback_agencies


@pytest.mark.parametrize("clip_quantile", [None, 0.9])
def test_a_sliced_week_matches_training_frame_at_its_own_cutoff(clip_quantile):
    """Pins the prefix property run_backtest's single training_frame build relies
    on: an earlier cutoff's own training_frame call must equal the later cutoff's
    frame, filtered to that cutoff and reweighted from it. Clipping keeps it, as
    each row's cap comes from its own origin's window, never from the cutoff."""
    runs = with_route_ids(_runs(), route_ids(_runs()))
    early_cutoff = START + timedelta(days=20)
    late_cutoff = START + timedelta(days=60)
    options = {"window_days": FAST.window_days, "half_life_days": FAST.half_life_days, "clip_quantile": clip_quantile}

    direct = training_frame(runs, early_cutoff, **options)
    sliced = training_frame(runs, late_cutoff, **options)
    sliced = sliced[sliced["service_date"] <= pd.Timestamp(early_cutoff)]
    age_days = (pd.Timestamp(early_cutoff) - sliced["service_date"]).dt.days
    sliced = sliced.assign(weight=(0.5 ** (age_days / FAST.half_life_days)).astype("float32"))

    sort_cols = ["agency_id", "trip_id", "service_date"]
    direct = direct.sort_values(sort_cols).reset_index(drop=True)
    sliced = sliced.sort_values(sort_cols).reset_index(drop=True)
    pd.testing.assert_frame_equal(direct, sliced)


def test_training_frame_is_built_once_for_every_retrain_week(monkeypatch):
    import ml.model_backtest as model_backtest

    calls = []
    real_training_frame = model_backtest.training_frame

    def spy(*args, **kwargs):
        calls.append(1)
        return real_training_frame(*args, **kwargs)

    monkeypatch.setattr(model_backtest, "training_frame", spy)
    run_backtest(_runs(), params=FAST, origin_count=28)
    assert len(calls) == 1


def _with_outliers():
    runs = _runs()
    late = runs.index % 37 == 0
    return runs.assign(delay_min=runs["delay_min"].where(~late, 90.0).astype("float32"))


def _b0_scores(result):
    rows = {(a.agency_id, r.horizon, r.peak, r.sparse): r for a in result.agencies for r in a.rows if r.method == "B0"}
    return {key: (r.errors.abs_err, r.errors.weight) for key, r in rows.items()}, result.intervals["B0"]


@pytest.mark.parametrize(
    "variant",
    [
        {"clip_quantile": 0.95},
        {"mean_objective": "huber", "point": "mean"},
        {"point": "mean"},
        {"clip_quantile": 0.95, "point": "mean"},
    ],
)
def test_every_variant_is_scored_against_the_same_raw_b0(variant):
    runs = _with_outliers()
    live = run_backtest(runs, params=FAST, origin_count=7)
    other = run_backtest(runs, params=replace(FAST, **variant), origin_count=7)
    assert other.origins == live.origins
    assert _b0_scores(other) == _b0_scores(live)
    assert other.params != live.params


def test_one_fit_scores_each_point_forecast_as_its_own_result():
    runs = _with_outliers()
    both = run_backtests(runs, params=FAST, origin_count=7, points=("mean", "q50"))
    assert both["mean"] == run_backtest(runs, params=replace(FAST, point="mean"), origin_count=7)
    assert both["q50"] == run_backtest(runs, params=replace(FAST, point="q50"), origin_count=7)
    assert both["mean"].intervals == both["q50"].intervals
    model_errors = {
        p: sum(r.errors.abs_err for a in both[p].agencies for r in a.rows if r.method == MODEL) for p in both
    }
    assert model_errors["mean"] != model_errors["q50"]


def test_the_published_slot_interval_is_scored_beside_the_per_run_one():
    result = run_backtest(_runs(), params=FAST, origin_count=7)
    per_run = sum(s.runs for s in result.intervals[MODEL].values())
    assert per_run > 0 and sum(s.runs for s in result.intervals[SLOT_INTERVAL].values()) == per_run


def test_the_point_forecast_is_the_median_unless_asked_for_the_mean():
    result = run_backtest(_runs(), params=FAST, origin_count=7)
    assert result.params["point"] == "q50"
    assert result == run_backtests(_runs(), params=FAST, origin_count=7, points=("q50",))["q50"]


def _count_mean_boosters(monkeypatch):
    import ml.models

    objectives = []
    real = ml.models.lgb.train

    def spy(params, *args, **kwargs):
        objectives.append(params["objective"])
        return real(params, *args, **kwargs)

    monkeypatch.setattr(ml.models.lgb, "train", spy)
    return objectives


def test_a_median_point_backtest_never_trains_the_mean_booster(monkeypatch):
    objectives = _count_mean_boosters(monkeypatch)
    run_backtest(_runs(), params=FAST, origin_count=10)
    assert objectives and "regression" not in objectives


def test_scoring_the_mean_trains_one_mean_booster_per_retrain(monkeypatch):
    objectives = _count_mean_boosters(monkeypatch)
    result = run_backtests(_runs(), params=FAST, origin_count=10, points=("mean", "q50"))["q50"]
    assert objectives.count("regression") == len(result.cutoffs)
