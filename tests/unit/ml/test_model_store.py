from dataclasses import replace
from datetime import date, timedelta

import pytest

from tests.unit.ml.ml_group import require

pd = require("pandas")
np = require("numpy")
require("lightgbm")
require("pyarrow")

from ml.features import build_frame, route_ids, with_route_ids  # noqa: E402
from ml.model_store import load_model_set, load_routes, read_meta, replace_dir, stage_model_set  # noqa: E402
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
    return frame


def _fitted(params=FAST):
    runs = _runs()
    routes = route_ids(runs)
    runs = with_route_ids(runs, routes)
    cutoff = START + timedelta(days=60)
    train = training_frame(runs, cutoff, window_days=28, half_life_days=28)
    return fit_calibrated(train, params, cutoff), routes, build_frame(runs, cutoff + timedelta(days=1))


def _save(models, directory, routes, version="2026-06-30", point="q50", clip_quantile=None):
    staged = stage_model_set(
        models, directory, version=version, routes=routes, window_days=28, point=point, clip_quantile=clip_quantile
    )
    replace_dir(staged, directory)


@pytest.mark.parametrize("point", ["q50", "mean"])
def test_a_saved_model_set_predicts_the_same_as_before_saving(tmp_path, point):
    models, routes, frame = _fitted(replace(FAST, point=point))
    before = predict(models, frame)

    directory = tmp_path / "model"
    _save(models, directory, routes, point=point)
    loaded = load_model_set(directory)
    after = predict(loaded, frame)

    if point == "mean":
        np.testing.assert_allclose(before.mean, after.mean)
    else:
        assert not (directory / "mean.txt").exists() and loaded.mean is None and after.mean is None
    np.testing.assert_allclose(before.q10, after.q10)
    np.testing.assert_allclose(before.q50, after.q50)
    np.testing.assert_allclose(before.q90, after.q90)
    assert loaded.interval_shift == models.interval_shift
    assert read_meta(directory) == {
        "interval_shift": models.interval_shift,
        "version": "2026-06-30",
        "window_days": 28,
        "point": point,
        "clip_quantile": None,
    }


def test_the_meta_records_the_clip_and_point_it_is_given(tmp_path):
    models, routes, _ = _fitted()
    _save(models, tmp_path / "model", routes, clip_quantile=0.99)
    assert (read_meta(tmp_path / "model")["point"], read_meta(tmp_path / "model")["clip_quantile"]) == ("q50", 0.99)


def test_a_mean_point_is_never_published_without_its_mean_booster(tmp_path):
    models, routes, _ = _fitted()
    with pytest.raises(ValueError, match="mean"):
        _save(models, tmp_path / "model", routes, point="mean")
    assert not (tmp_path / "model").exists()


def test_the_route_vocabulary_round_trips(tmp_path):
    runs = _runs()
    routes = route_ids(runs)
    runs = with_route_ids(runs, routes)
    cutoff = START + timedelta(days=60)
    train = training_frame(runs, cutoff, window_days=28, half_life_days=28)
    models = fit_calibrated(train, FAST, cutoff)

    directory = tmp_path / "model"
    _save(models, directory, routes)
    loaded_routes = load_routes(directory)

    pd.testing.assert_frame_equal(
        loaded_routes.sort_values("route_code").reset_index(drop=True),
        routes.sort_values("route_code").reset_index(drop=True),
        check_dtype=False,  # a parquet round trip changes pandas' nullable-string NA flavor, not the values
    )


def test_a_failed_save_leaves_the_previously_published_model_untouched(tmp_path, monkeypatch):
    runs = _runs()
    routes = route_ids(runs)
    runs = with_route_ids(runs, routes)
    cutoff = START + timedelta(days=60)
    train = training_frame(runs, cutoff, window_days=28, half_life_days=28)
    models = fit_calibrated(train, FAST, cutoff)

    directory = tmp_path / "model"
    _save(models, directory, routes, version="v1")
    original = (directory / "q0.5.txt").read_bytes()

    def boom(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(pd.DataFrame, "to_parquet", boom)
    with pytest.raises(RuntimeError):
        _save(models, directory, routes, version="v2")

    assert (directory / "q0.5.txt").read_bytes() == original
    assert read_meta(directory)["version"] == "v1"
