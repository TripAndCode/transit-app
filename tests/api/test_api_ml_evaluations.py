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
    assert body["latest"] is None and body["history"] == []


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
