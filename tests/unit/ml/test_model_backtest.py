from datetime import date, timedelta

from tests.unit.ml.ml_group import require

pd = require("pandas")
np = require("numpy")
require("lightgbm")

from ml.features import route_ids, with_route_ids  # noqa: E402
from ml.model_backtest import run_backtest  # noqa: E402
from ml.model_result import result_from_json, result_to_json  # noqa: E402
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


def test_a_sliced_week_matches_training_frame_at_its_own_cutoff():
    """Pins the prefix property run_backtest's single training_frame build relies
    on: an earlier cutoff's own training_frame call must equal the later cutoff's
    frame, filtered to that cutoff and reweighted from it."""
    runs = with_route_ids(_runs(), route_ids(_runs()))
    early_cutoff = START + timedelta(days=20)
    late_cutoff = START + timedelta(days=60)

    direct = training_frame(runs, early_cutoff, window_days=FAST.window_days, half_life_days=FAST.half_life_days)
    sliced = training_frame(runs, late_cutoff, window_days=FAST.window_days, half_life_days=FAST.half_life_days)
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
