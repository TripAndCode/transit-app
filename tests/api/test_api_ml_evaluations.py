"""GET /api/ml/evaluations: the newest week's fields, the history, and
whether each agency's ML forecast is live on the heatmap right now."""

import json
from datetime import date, timedelta

from api.range import jst_today
from pipeline import flags

_FORECAST_COLUMNS = (
    "agency_id, route_code, service_date, hour, mean_min, q10, q50, q90, model_version, origin_date, generated_at"
)


def _summary(run_date: str, adopted: bool, skill: float, agency_id: int) -> str:
    return json.dumps(
        {
            "schema": 1,
            "run_date": run_date,
            "adopted": adopted,
            "failures": [] if adopted else [{"code": "skill_below_min", "value": skill, "agencies": []}],
            "headline": {"skill": skill, "coverage": 0.8},
            "thresholds": {"min_skill": 0.05, "coverage_low": 0.75, "coverage_high": 0.85, "min_model_days": 56},
            "point_metric": "mean",
            "interval": {"LGBM": {"coverage": 0.8, "mean_width": 3.0}},
            "origin_skill": {"count": 28, "min": -0.3, "median": 0.06, "max": 0.18},
            "agencies": [
                {
                    "agency_id": agency_id,
                    "model_status": "model",
                    "skill": 0.07,
                    "lgbm_mae": 1.0,
                    "b0_mae": 1.1,
                    "days_of_data": 140,
                }
            ],
            "serving_model": {"version": "2026-10-17", "window_days": 28},
            "params": {"window_days": 28},
        }
    )


async def _seed_week(aconn, run_date: date, adopted: bool, skill: float, agency_id: int) -> None:
    await aconn.execute(
        "INSERT INTO ml_evaluations (run_date, schema_version, adopted, overall_skill, summary) "
        "VALUES ($1, 1, $2, $3, $4::jsonb)",
        run_date,
        adopted,
        skill,
        _summary(run_date.isoformat(), adopted, skill, agency_id),
    )


async def _seed_fresh_forecast(aconn, agency_id: int) -> None:
    today = jst_today()
    await aconn.execute(
        f"INSERT INTO agg_route_forecast_ml ({_FORECAST_COLUMNS}) "
        "VALUES ($1, 'R1', $2, 8, 3.0, 1.0, 3.0, 5.0, 'v1', $3, now())",
        agency_id,
        today + timedelta(days=1),
        today,
    )


async def test_no_evaluation_yet_is_an_empty_answer_not_an_error(client):
    response = await client.get("/api/ml/evaluations")
    assert response.status_code == 200
    body = response.json()
    assert body["latest"] is None and body["history"] == [] and body["live"] is None


async def test_the_newest_week_and_the_history_are_served(client, aconn, aagency_id, monkeypatch):
    monkeypatch.setenv("ML_FORECAST_ENABLED", "true")
    flags.reset_cache()
    await _seed_week(aconn, date(2026, 10, 18), True, 0.063, aagency_id)
    await _seed_week(aconn, date(2026, 10, 25), False, 0.046, aagency_id)
    await _seed_fresh_forecast(aconn, aagency_id)

    body = (await client.get("/api/ml/evaluations")).json()

    latest = body["latest"]
    assert latest["run_date"] == "2026-10-25"
    assert latest["adopted"] is False
    assert latest["failures"] == [{"code": "skill_below_min", "value": 0.046, "agencies": []}]
    assert latest["agencies"][0]["agency_name"]  # joined from agencies
    assert latest["agencies"][0]["forecast_live"] is True
    assert "params" not in latest
    assert [w["run_date"] for w in body["history"]] == ["2026-10-25", "2026-10-18"]
    assert body["forecast_in_use"] is True


async def test_with_the_flag_off_nothing_is_live(client, aconn, aagency_id, monkeypatch):
    monkeypatch.setenv("ML_FORECAST_ENABLED", "false")
    flags.reset_cache()
    await _seed_week(aconn, date(2026, 10, 25), True, 0.051, aagency_id)
    await _seed_fresh_forecast(aconn, aagency_id)

    body = (await client.get("/api/ml/evaluations")).json()
    assert body["forecast_in_use"] is False
    assert body["latest"]["agencies"][0]["forecast_live"] is False


async def test_a_stale_forecast_is_not_live(client, aconn, aagency_id, monkeypatch):
    monkeypatch.setenv("ML_FORECAST_ENABLED", "true")
    flags.reset_cache()
    await _seed_week(aconn, date(2026, 10, 25), True, 0.051, aagency_id)
    stale_origin = jst_today() - timedelta(days=10)
    await aconn.execute(
        f"INSERT INTO agg_route_forecast_ml ({_FORECAST_COLUMNS}) "
        "VALUES ($1, 'R1', $2, 8, 3.0, 1.0, 3.0, 5.0, 'v1', $3, now())",
        aagency_id,
        jst_today() + timedelta(days=1),
        stale_origin,
    )

    body = (await client.get("/api/ml/evaluations")).json()
    assert body["latest"]["agencies"][0]["forecast_live"] is False


async def test_the_newest_live_scorecard_is_served(client, aconn, aagency_id):
    await _seed_week(aconn, date(2026, 11, 8), True, 0.06, aagency_id)
    for run_date, skill in ((date(2026, 11, 1), 0.004), (date(2026, 11, 8), 0.012)):
        document = {
            "schema": 1,
            "run_date": run_date.isoformat(),
            "window": {"first_origin": "2026-10-11", "last_origin": "2026-11-05", "origins": 26, "target_days": 25},
            "overall": {"live": {"mae": 1.2, "rmse": 2.3, "runs": 41000}},
            "skill": {"live": skill, "counterfactual": 0.061},
            "interval": {"live_slot": {"coverage": 0.77, "mean_width": 3.1}},
            "published_share": 0.93,
            "by_lead": [{"lead": 1, "skill_live": -0.01, "skill_counterfactual": 0.05, "runs": 6000}],
        }
        await aconn.execute(
            "INSERT INTO ml_live_evaluations (run_date, schema_version, document) VALUES ($1, 1, $2::jsonb)",
            run_date,
            json.dumps(document),
        )

    live = (await client.get("/api/ml/evaluations")).json()["live"]
    assert (live["skill_live"], live["skill_counterfactual"]) == (0.012, 0.061)  # the newest week's
    assert (live["origins"], live["target_days"]) == (26, 25)
    assert (live["live_slot_coverage"], live["published_share"]) == (0.77, 0.93)
    assert live["by_lead"] == [{"lead": 1, "skill_live": -0.01, "skill_counterfactual": 0.05}]
