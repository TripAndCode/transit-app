"""The weekly ML evaluation as the API serves it: the fields the model page
reads, picked out of the stored summary (never the raw document), with each
agency's name and whether its ML forecast is live on the heatmap joined in."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

# The interval `ml predict` publishes per route×date×hour slot; summaries
# from before it existed carry only the per-run interval.
_SLOT_INTERVAL = "LGBM slot"
_RUN_INTERVAL = "LGBM"


def _interval_coverage(summary: Mapping[str, Any]) -> float | None:
    interval = summary.get("interval") or {}
    chosen = interval.get(_SLOT_INTERVAL) or interval.get(_RUN_INTERVAL) or {}
    return chosen.get("coverage")


def shape_latest(
    summary: Mapping[str, Any], run_date: date, names: Mapping[int, str], live: set[int]
) -> dict[str, Any]:
    headline = summary.get("headline") or {}
    serving = summary.get("serving_model") or {}
    return {
        "run_date": run_date,
        "adopted": bool(summary.get("adopted")),
        "failures": [
            {"code": f.get("code"), "value": f.get("value"), "agencies": list(f.get("agencies") or [])}
            for f in summary.get("failures") or []
        ],
        "skill": headline.get("skill"),
        "coverage": headline.get("coverage"),
        "interval_coverage": _interval_coverage(summary),
        "thresholds": dict(summary.get("thresholds") or {}),
        "point_metric": summary.get("point_metric") or "mean",
        "serving_model_version": serving.get("version"),
        "origin_skill": dict(summary.get("origin_skill") or {}),
        "agencies": [
            {
                "agency_id": a["agency_id"],
                "agency_name": names.get(a["agency_id"]),
                "model_status": a.get("model_status", "b0"),
                "skill": a.get("skill"),
                "lgbm_mae": a.get("lgbm_mae"),
                "b0_mae": a.get("b0_mae"),
                "days_of_data": a.get("days_of_data"),
                "forecast_live": a["agency_id"] in live,
            }
            for a in summary.get("agencies") or []
        ],
    }


def shape_history(rows: Sequence[tuple[date, bool, float | None, str]]) -> list[dict[str, Any]]:
    return [
        {"run_date": run_date, "adopted": adopted, "overall_skill": skill, "point_metric": point_metric}
        for run_date, adopted, skill, point_metric in sorted(rows, key=lambda row: row[0], reverse=True)
    ]
