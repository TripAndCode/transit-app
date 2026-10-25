from dataclasses import replace
from datetime import date, timedelta

import pytest

from tests.unit.ml.ml_group import require

pd = require("pandas")
np = require("numpy")
require("lightgbm")

from ml.compare import FITS, compare, render_markdown, variant_name  # noqa: E402
from ml.model_backtest import run_backtests  # noqa: E402
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
            "delay_min": 90.0 if (d * 3 + trip) % 41 == 0 else base + rng.normal(0, 0.5),
            "stops": 10,
            "span_min": 30.0,
        }
        for d in range(days)
        for agency, route, base in ((8, "R1", 3.0), (9, "B1", 2.0))
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


def _results(runs, origin_count=7):
    results = {}
    for fit in ("base", "clip"):
        params = replace(FAST, **FITS[fit])
        for point, result in run_backtests(
            runs, params=params, origin_count=origin_count, points=("mean", "q50")
        ).items():
            results[variant_name(fit, point)] = result
    return results


def test_a_variant_is_named_by_its_fit_and_point():
    assert variant_name("base", "mean") == "base"
    assert variant_name("clip", "q50") == "clip-q50"


def test_the_base_fit_is_the_default_model():
    assert replace(FAST, **FITS["base"]) == FAST


def test_each_variant_is_summarized_against_b0_and_the_baseline_variant():
    comparison = compare(_results(_runs()), baseline="base")
    assert set(comparison["variants"]) == {"base", "base-q50", "clip", "clip-q50"}
    base, clip = comparison["variants"]["base"], comparison["variants"]["clip"]
    assert base["b0_mae"] == clip["b0_mae"]
    assert set(base["agency_skill"]) == {"8", "9"}
    assert set(base["horizon_mae"]) == {str(h) for h in range(1, 8)}
    assert base["origin_wins"] == {"wins": 0, "ties": 7, "losses": 0}
    assert sum(clip["origin_wins"].values()) == 7
    for summary in comparison["variants"].values():
        assert summary["coverage"] is not None and summary["slot_coverage"] is not None
        assert isinstance(summary["adopted"], bool)
    assert comparison["origins"][0] < comparison["origins"][-1]


def test_variants_scored_on_different_origins_are_refused():
    runs = _runs()
    results = _results(runs)
    results["short"] = run_backtests(runs, params=FAST, origin_count=5, points=("mean",))["mean"]
    with pytest.raises(ValueError, match="origins"):
        compare(results, baseline="base")


def test_a_missing_baseline_is_refused():
    with pytest.raises(ValueError, match="baseline"):
        compare(_results(_runs()), baseline="huber")


def test_the_markdown_names_every_variant_and_agency():
    text = render_markdown(compare(_results(_runs()), baseline="base"))
    for name in ("base", "base-q50", "clip", "clip-q50", "agency 8", "agency 9"):
        assert name in text
