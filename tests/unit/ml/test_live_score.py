"""Scoring a published live forecast against what ran, beside B0 as of its origin and the counterfactual."""

import json
from datetime import date, datetime, timedelta

from tests.unit.ml.ml_group import require

pd = require("pandas")
np = require("numpy")

from ml.features import route_ids, weekend_days  # noqa: E402
from ml.live_score import (  # noqa: E402
    Scorecard,
    counterfactual_frame,
    score_origin,
    to_json,
    window_origins,
)
from ml.models import Predictions  # noqa: E402

ORIGIN = date(2026, 11, 1)


def _frame(rows):
    """Counterfactual frame rows: (route, day offset, hour, delay, b0)."""
    frame = pd.DataFrame(
        [
            {
                "agency_id": 8,
                "route_code": route,
                "service_date": pd.Timestamp(ORIGIN + timedelta(days=h)),
                "hour": hour,
                "h": h,
                "delay_min": delay,
                "b0": b0,
                "slot_p10": None if b0 is None else b0 - 1.0,
                "slot_p90": None if b0 is None else b0 + 1.0,
            }
            for route, h, hour, delay, b0 in rows
        ]
    )
    return frame.astype(
        {
            "agency_id": "int16",
            "route_code": "string",
            "hour": "int16",
            "b0": "float32",
            "slot_p10": "float32",
            "slot_p90": "float32",
            "delay_min": "float32",
        }
    )


def _preds(point, width=1.0):
    point = np.asarray(point, dtype=float)
    return Predictions(mean=None, q10=point - width, q50=point, q90=point + width)


def _forecast(rows):
    """Published cells: (route, day offset, hour, mean_min)."""
    frame = pd.DataFrame(
        [
            {
                "agency_id": 8,
                "route_code": route,
                "service_date": pd.Timestamp(ORIGIN + timedelta(days=h)),
                "hour": hour,
                "mean_min": live,
                "q10": live - 1.0,
                "q90": live + 1.0,
                "model_version": "2026-10-31",
            }
            for route, h, hour, live in rows
        ]
    )
    return frame.astype({"agency_id": "int16", "route_code": "string", "hour": "int16"})


def _score(frame, point, forecast, final_through=ORIGIN + timedelta(days=7), withheld=0):
    card = Scorecard()
    score_origin(
        card,
        ORIGIN,
        "2026-10-31",
        forecast,
        frame,
        _preds(point),
        np.asarray(point, dtype=float),
        final_through,
        withheld,
    )
    return card


def test_errors_are_run_weighted_means_of_each_cell():
    # One cell, two runs (1 and 3, actual mean 2): live 4, counterfactual 2, B0 1.
    frame = _frame([("R1", 1, 8, 1.0, 1.0), ("R1", 1, 8, 3.0, 1.0)])
    card = _score(frame, [2.0, 2.0], _forecast([("R1", 1, 8, 4.0)]))
    assert card.overall.live.mae == 2.0
    assert card.overall.counterfactual.mae == 0.0
    assert card.overall.b0.mae == 1.0
    assert card.overall.b0.weight == 2
    assert card.overall.skill("live") == -1.0
    assert card.overall.skill("counterfactual") == 1.0


def test_only_cells_the_forecast_published_and_that_ran_are_scored():
    # R1 published and ran; R2 ran unpublished; R3 published but never ran.
    frame = _frame([("R1", 1, 8, 2.0, 1.0), ("R2", 1, 8, 2.0, 1.0)])
    card = _score(frame, [2.0, 2.0], _forecast([("R1", 1, 8, 2.0), ("R3", 1, 8, 2.0)]))
    assert card.overall.b0.weight == 1
    assert card.shares[8].published_runs == 1
    assert card.shares[8].actual_runs == 2


def test_a_cell_without_b0_history_is_not_paired_but_counts_as_published():
    frame = _frame([("R1", 1, 8, 2.0, None)])
    card = _score(frame, [2.0], _forecast([("R1", 1, 8, 2.0)]))
    assert card.overall.b0.weight == 0
    assert card.shares[8].published_runs == 1


def test_lead_days_bucket_by_days_after_the_origin():
    frame = _frame([("R1", 1, 8, 2.0, 1.0), ("R1", 3, 8, 2.0, 1.0)])
    card = _score(frame, [2.0, 2.0], _forecast([("R1", 1, 8, 2.0), ("R1", 3, 8, 2.0)]))
    assert sorted(card.by_lead) == [1, 3]


def test_a_target_day_not_yet_final_is_not_scored():
    frame = _frame([("R1", 1, 8, 2.0, 1.0), ("R1", 5, 8, 2.0, 1.0)])
    card = _score(
        frame, [2.0, 2.0], _forecast([("R1", 1, 8, 2.0), ("R1", 5, 8, 2.0)]), final_through=ORIGIN + timedelta(days=2)
    )
    assert sorted(card.by_lead) == [1]


def test_interval_coverage_counts_each_run_inside_its_bounds():
    # Live bounds 1..3 (cell), counterfactual per-run bounds point±1: runs 2 and 9.
    frame = _frame([("R1", 1, 8, 2.0, 2.0), ("R1", 1, 8, 9.0, 2.0)])
    card = _score(frame, [2.0, 2.0], _forecast([("R1", 1, 8, 2.0)]))
    assert card.intervals["live_slot"].coverage == 0.5
    assert card.intervals["counterfactual"].coverage == 0.5
    assert card.intervals["B0"].coverage == 0.5


def test_window_origins_are_the_published_origins_with_a_final_target_day():
    # run_date 11/8: the window is 10/11 (28 days back) .. 11/5 (its first target day, 11/6, is final).
    days = ("2026-10-10", "2026-10-11", "2026-11-05", "2026-11-06")
    keys = [f"forecasts/{d}/{name}" for d in days for name in ("forecast.parquet", "manifest.json")]
    assert window_origins(keys, date(2026, 11, 8)) == [date(2026, 10, 11), date(2026, 11, 5)]


def test_an_origin_whose_manifest_never_landed_is_not_scored():
    # predict uploads the manifest after the forecast: without it, the publish did not finish.
    keys = ["forecasts/2026-11-01/forecast.parquet", "forecasts/2026-11-01/manifest.json"]
    keys.append("forecasts/2026-11-02/forecast.parquet")
    assert window_origins(keys, date(2026, 11, 8)) == [date(2026, 11, 1)]


def test_the_counterfactual_frame_reads_no_target_day_into_b0():
    """History delays are 1; the target day's are 100 and origin−1's after-midnight run is 50: B0 as of the
    origin sees only the 1s."""
    rows = []
    for d in range(-28, 2):
        day = ORIGIN + timedelta(days=d)
        delay = 100.0 if d == 1 else 1.0
        rows.append(("T1", day, 8, delay))
    rows.append(("T2", ORIGIN - timedelta(days=1), 25, 50.0))
    runs = pd.DataFrame(
        [
            {
                "agency_id": 8,
                "route_code": "R1",
                "trip_id": trip,
                "service_date": pd.Timestamp(day),
                "hour": hour,
                "service": "wk",
                "delay_min": delay,
                "stops": 10,
                "span_min": 30.0,
            }
            for trip, day, hour, delay in rows
        ]
    ).astype(
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
    frame = counterfactual_frame(runs, route_ids(runs), ORIGIN, {"window_days": 28}, weekend_days(runs))
    target = frame[frame["service_date"] == pd.Timestamp(ORIGIN + timedelta(days=1))]
    assert len(target) == 1
    assert float(target["b0"].iloc[0]) == 1.0
    assert float(target["route_mean"].iloc[0]) == 1.0


def test_the_document_reports_each_bucket_and_is_finite():
    frame = _frame([("R1", 1, 8, 2.0, 1.0), ("R1", 2, 9, 4.0, 3.0)])
    card = _score(frame, [2.0, 3.0], _forecast([("R1", 1, 8, 2.5), ("R1", 2, 9, 3.5)]), withheld=1)
    doc = to_json(card, run_date=date(2026, 11, 10), generated_at=datetime(2026, 11, 10, 7, 0))
    json.dumps(doc, allow_nan=False)
    assert doc["schema"] == 1
    assert doc["window"] == {"first_origin": "2026-11-01", "last_origin": "2026-11-01", "origins": 1, "target_days": 2}
    assert set(doc["overall"]) == {"live", "counterfactual", "B0"}
    assert [row["lead"] for row in doc["by_lead"]] == [1, 2]
    assert doc["agencies"][0]["agency_id"] == 8
    assert doc["agencies"][0]["first_published_origin"] == "2026-11-01"
    assert doc["origins"][0]["withheld_days"] == 1
    assert doc["published_share"] == 1.0


def test_an_empty_scorecard_is_a_valid_document():
    doc = to_json(Scorecard(), run_date=date(2026, 11, 10), generated_at=datetime(2026, 11, 10, 7, 0))
    json.dumps(doc, allow_nan=False)
    assert doc["window"] == {"origins": 0, "target_days": 0}
    assert doc["overall"] == {}
    assert doc["by_lead"] == [] and doc["agencies"] == [] and doc["origins"] == []
    assert "published_share" not in doc
