from tests.unit.ml.ml_group import require

lightgbm = require("lightgbm")
np = require("numpy")
pd = require("pandas")


def test_the_ml_group_trains_a_model():
    x = np.arange(200, dtype=float).reshape(-1, 1)
    data = lightgbm.Dataset(x, label=2 * x[:, 0])
    booster = lightgbm.train(
        {"objective": "regression", "verbose": -1, "min_data_in_leaf": 5}, data, num_boost_round=30
    )
    assert abs(booster.predict(np.array([[100.0]]))[0] - 200) < 20


def test_pandas_reads_the_frames_the_models_use():
    frame = pd.DataFrame({"x": [1.0, 2.0]})
    assert frame["x"].mean() == 1.5
