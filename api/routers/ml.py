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
from api.routers.reports import fetch_live_ml_agencies
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


@router.get("/evaluations", response_model=MlEvaluationsResponse)
@limiter.limit(tier_limit)
async def ml_evaluations(request: Request, conn: asyncpg.Connection = Depends(get_conn)) -> dict[str, Any]:
    """Every week the workbench evaluated, newest first, with the newest
    week's detail. `forecast_live` uses the heatmap's own freshness rule, so
    it says whether that agency's heatmap shows ML values right now."""
    weeks = await conn.fetch(
        "SELECT run_date, adopted, overall_skill, COALESCE(summary->>'point_metric', 'mean') AS point_metric "
        "FROM ml_evaluations ORDER BY run_date DESC"
    )
    forecast_in_use = await aflag("ml_forecast")
    if not weeks:
        return {"latest": None, "history": [], "forecast_in_use": forecast_in_use}
    newest = weeks[0]["run_date"]
    summary = json.loads(await conn.fetchval("SELECT summary FROM ml_evaluations WHERE run_date = $1", newest))
    names = {r["agency_id"]: r["agency_name"] for r in await conn.fetch("SELECT agency_id, agency_name FROM agencies")}
    live = await fetch_live_ml_agencies(conn) if forecast_in_use else set()
    return {
        "latest": shape_latest(summary, newest, names, live),
        "history": shape_history([(w["run_date"], w["adopted"], w["overall_skill"], w["point_metric"]) for w in weeks]),
        "forecast_in_use": forecast_in_use,
    }
