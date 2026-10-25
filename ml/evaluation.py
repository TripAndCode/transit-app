"""A week's model evaluation as the small JSON document the web app ingests:
the adoption verdict as data, the figures it judged, per-agency rows and the
model then being served. Built from the same `judge`/`summarize` the HTML
report and the `train` gate read, so the three never disagree. Readable
without the `ml` dependency group."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from ml.adoption import COVERAGE_RANGE, MIN_SKILL, judge
from ml.backtest import AgencyResult
from ml.model_result import (
    MIN_MODEL_DAYS,
    MODEL,
    IntervalStats,
    ModelBacktestResult,
    merged_intervals,
    origin_skill_spread,
)
from ml.summary import summarize

SCHEMA = 1
B0 = "B0"


def _figures(result: ModelBacktestResult, method: str, agency_id: int | None = None) -> dict[str, float | None]:
    s = summarize(result.agencies, method, agency_id=agency_id)
    return {"mae": s.mae, "rmse": s.rmse, "coverage": s.coverage}


def _interval(stats: IntervalStats) -> dict[str, float | None]:
    return {"coverage": stats.coverage, "mean_width": stats.mean_width}


def _model_status(agency: AgencyResult, on_b0_at_a_cutoff: set[int]) -> str:
    """`model` when the agency was on the model at every cutoff; `b0` when it
    was on B0 at every one; `partial` when it crossed the history threshold
    mid-backtest. On B0 the model's shown prediction is B0's own, so its
    paired errors repeat B0's exactly: that equality, not the fallback list
    (which holds an agency on B0 at any cutoff), tells `b0` from `partial`."""
    if agency.agency_id not in on_b0_at_a_cutoff:
        return "model"
    model_rows = [row for row in agency.rows if row.method == MODEL]
    if all(row.paired.abs_err == row.paired_b0.abs_err for row in model_rows):
        return "b0"
    return "partial"


def build_summary(
    result: ModelBacktestResult,
    *,
    run_date: date,
    generated_at: datetime,
    serving_meta: dict[str, Any] | None,
) -> dict[str, Any]:
    verdict = judge(result)
    on_b0_at_a_cutoff = set(result.fallback_agencies)
    model_intervals = result.intervals.get(MODEL, {})
    agencies = []
    for agency in result.agencies:
        status = _model_status(agency, on_b0_at_a_cutoff)
        interval = model_intervals.get(str(agency.agency_id))
        agencies.append(
            {
                "agency_id": agency.agency_id,
                "days_of_data": agency.days_of_data,
                "short_history": agency.short_history,
                "model_status": status,
                # On B0 throughout, the agency's 0% is how it was scored, not
                # how the model did; a partial agency's blended skill is real.
                "skill": None if status == "b0" else verdict.agency_skills.get(agency.agency_id),
                "lgbm_mae": _figures(result, MODEL, agency.agency_id)["mae"],
                "b0_mae": _figures(result, B0, agency.agency_id)["mae"],
                "interval_coverage": interval.coverage if interval else None,
            }
        )
    low, high = COVERAGE_RANGE
    return {
        "schema": SCHEMA,
        "run_date": run_date.isoformat(),
        "generated_at": generated_at.isoformat(),
        "adopted": verdict.adopted,
        "failures": [{"code": f.code, "value": f.value, "agencies": list(f.agencies)} for f in verdict.failures],
        "headline": {"skill": verdict.skill, "coverage": verdict.coverage},
        "thresholds": {
            "min_skill": MIN_SKILL,
            "coverage_low": low,
            "coverage_high": high,
            "min_model_days": MIN_MODEL_DAYS,
        },
        "point_metric": result.params.get("point", "mean"),
        "params": result.params,
        "overall": {MODEL: _figures(result, MODEL), B0: _figures(result, B0)},
        "interval": {method: _interval(merged_intervals(result, method)) for method in result.intervals},
        "origin_skill": origin_skill_spread(result),
        "agencies": agencies,
        "serving_model": serving_meta,
    }
