"""The ML forecast model's weekly evaluation, for the model page: the newest
week's verdict and figures, the history of past weeks, and whether each
agency's ML forecast is live on the heatmap now."""

from __future__ import annotations

import json
from datetime import date
from typing import Any, Literal

import asyncpg
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from api.deps import get_conn
from api.middleware.ratelimit import limiter, tier_limit
from api.routers.reports import ML_FRESHNESS_DAYS, ML_HORIZON_DAYS
from pipeline.flags import aflag
from pipeline.reports.ml_evaluation import shape_history, shape_latest

router = APIRouter(prefix="/api/ml", tags=["ml"])


class MlFailure(BaseModel):
    code: str
    value: float | None
    agencies: list[int]


class MlThresholds(BaseModel):
    min_skill: float | None = None
    coverage_low: float | None = None
    coverage_high: float | None = None
    min_model_days: int | None = None


class MlOriginSkill(BaseModel):
    count: int | None = None
    min: float | None = None
    median: float | None = None
    max: float | None = None


class MlAgency(BaseModel):
    agency_id: int
    agency_name: str | None
    model_status: Literal["model", "partial", "b0"]
    skill: float | None
    lgbm_mae: float | None
    b0_mae: float | None
    days_of_data: int | None
    forecast_live: bool


class MlEvaluationLatest(BaseModel):
    run_date: date
    adopted: bool
    failures: list[MlFailure]
    skill: float | None
    coverage: float | None
    interval_coverage: float | None
    thresholds: MlThresholds
    point_metric: str
    serving_model_version: str | None
    origin_skill: MlOriginSkill
    agencies: list[MlAgency]


class MlEvaluationWeek(BaseModel):
    run_date: date
    adopted: bool
    overall_skill: float | None
    point_metric: str


class MlEvaluationsResponse(BaseModel):
    latest: MlEvaluationLatest | None
    history: list[MlEvaluationWeek]
    forecast_in_use: bool


def _document(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else json.loads(value)


@router.get("/evaluations", response_model=MlEvaluationsResponse)
@limiter.limit(tier_limit)
async def ml_evaluations(request: Request, conn: asyncpg.Connection = Depends(get_conn)) -> dict[str, Any]:
    """Every week the workbench evaluated, newest first, with the newest
    week's detail. `forecast_live` uses the heatmap's own freshness rule, so
    it says whether that agency's heatmap shows ML values right now."""
    rows = await conn.fetch(
        "SELECT run_date, adopted, overall_skill, summary FROM ml_evaluations ORDER BY run_date DESC"
    )
    forecast_in_use = await aflag("ml_forecast")
    if not rows:
        return {"latest": None, "history": [], "forecast_in_use": forecast_in_use}
    names = {r["agency_id"]: r["agency_name"] for r in await conn.fetch("SELECT agency_id, agency_name FROM agencies")}
    live: set[int] = set()
    if forecast_in_use:
        live = {
            r["agency_id"]
            for r in await conn.fetch(
                "SELECT DISTINCT agency_id FROM agg_route_forecast_ml "
                "WHERE service_date > now()::date AND service_date <= now()::date + $1::int "
                "AND origin_date >= now()::date - $2::int AND hour < 24",
                ML_HORIZON_DAYS,
                ML_FRESHNESS_DAYS,
            )
        }
    newest = rows[0]
    return {
        "latest": shape_latest(_document(newest["summary"]), newest["run_date"], names, live),
        "history": shape_history(
            [(r["run_date"], r["adopted"], r["overall_skill"], _document(r["summary"])) for r in rows]
        ),
        "forecast_in_use": forecast_in_use,
    }
