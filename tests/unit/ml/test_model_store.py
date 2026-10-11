from datetime import date, timedelta

from tests.unit.ml.ml_group import require

pd = require("pandas")
np = require("numpy")
require("lightgbm")

from ml.features import build_frame, route_ids, with_route_ids  # noqa: E402
from ml.model_store import load_model_set, save_model_set  # noqa: E402
from ml.models import ModelParams, fit_calibrated, predict  # noqa: E402
from ml.training import training_frame  # noqa: E402

START = date(2026, 6, 1)
FAST = ModelParams(rounds=40, num_leaves=7, min_data_in_leaf=5)


def _runs(days=70):
    rng = np.random.default_rng(3)
    rows = [
        {
            "agency_id": 8,
            "route_code": route,
            "trip_id": f"{route}{trip}",
            "service_date": pd.Timestamp(START + timedelta(days=d)),
            "hour": 8 + trip,
            "service": "wk",
            "delay_min": base + rng.normal(0, 0.3),
            "stops": 10,
            "span_min": 30.0,
        }
        for d in range(days)
        for route, base in (("LATE", 5.0), ("EARLY", 1.0))
        for trip in range(3)
    ]
    frame = pd.DataFrame(rows).astype(
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
    return with_route_ids(frame, route_ids(frame))


def test_a_saved_model_set_predicts_the_same_as_before_saving(tmp_path):
    runs = _runs()
    cutoff = START + timedelta(days=60)
    train = training_frame(runs, cutoff, window_days=28, half_life_days=28)
    models = fit_calibrated(train, FAST, cutoff)
    frame = build_frame(runs, cutoff + timedelta(days=1))
    before = predict(models, frame)

    directory = tmp_path / "model"
    save_model_set(models, directory)
    loaded = load_model_set(directory)
    after = predict(loaded, frame)

    np.testing.assert_allclose(before.mean, after.mean)
    np.testing.assert_allclose(before.q10, after.q10)
    np.testing.assert_allclose(before.q50, after.q50)
    np.testing.assert_allclose(before.q90, after.q90)
    assert loaded.interval_shift == models.interval_shift
