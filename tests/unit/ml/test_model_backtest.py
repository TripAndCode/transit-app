from datetime import date, timedelta

from tests.unit.ml.ml_group import require

pd = require("pandas")
np = require("numpy")
require("lightgbm")

from ml.model_backtest import run_backtest  # noqa: E402
from ml.model_result import result_from_json, result_to_json  # noqa: E402
from ml.models import ModelParams  # noqa: E402

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
