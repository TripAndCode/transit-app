"""Scoring the published live forecasts against what ran. Each forecast cell is scored beside B0 as of
the forecast's own origin and beside the counterfactual: the same published model given the target day's
actual runs instead of its template. Live minus counterfactual is the template's cost; the
counterfactual against B0 is the model's own skill. Needs the optional `ml` dependency group."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from ml.features import build_frame, with_route_ids
from ml.metrics import ErrorStats
from ml.model_result import IntervalStats
from ml.models import Predictions
from ml.training import HORIZON_DAYS
from pipeline.service_day import SERVICE_DAY_CUTOVER_HOURS

SCHEMA = 1
KEY = ["agency_id", "route_code", "service_date", "hour"]
INTERVALS = ("live_slot", "counterfactual", "counterfactual_slot", "B0")
_FORECAST_KEY = re.compile(r"^forecasts/(\d{4}-\d{2}-\d{2})/forecast\.parquet$")


@dataclass
class Bucket:
    """Run-weighted errors on the paired cells: those the forecast published, that ran, and that B0 had
    history for."""

    live: ErrorStats = field(default_factory=ErrorStats)
    counterfactual: ErrorStats = field(default_factory=ErrorStats)
    b0: ErrorStats = field(default_factory=ErrorStats)

    def add(self, *, live: float, counterfactual: float, b0: float, actual: float, runs: int) -> None:
        self.live.add(live - actual, runs)
        self.counterfactual.add(counterfactual - actual, runs)
        self.b0.add(b0 - actual, runs)

    def skill(self, method: str) -> float | None:
        mae, base = getattr(self, method).mae, self.b0.mae
        return None if mae is None or not base else 1 - mae / base


@dataclass
class Share:
    published_runs: int = 0
    actual_runs: int = 0


@dataclass
class Scorecard:
    overall: Bucket = field(default_factory=Bucket)
    by_lead: dict[int, Bucket] = field(default_factory=dict)
    by_agency: dict[int, Bucket] = field(default_factory=dict)
    intervals: dict[str, IntervalStats] = field(default_factory=lambda: {name: IntervalStats() for name in INTERVALS})
    shares: dict[int, Share] = field(default_factory=dict)
    first_published: dict[int, date] = field(default_factory=dict)
    origins: list[dict] = field(default_factory=list)
    target_days: set[date] = field(default_factory=set)


def window_origins(keys: list[str], run_date: date, *, days: int = 28) -> list[date]:
    """The published origins in the last *days* whose first target day is final (from day + 2)."""
    first, last = run_date - timedelta(days=days), run_date - timedelta(days=3)
    origins = {date.fromisoformat(match[1]) for key in keys if (match := _FORECAST_KEY.match(key))}
    return sorted(origin for origin in origins if first <= origin <= last)


def counterfactual_frame(runs: pd.DataFrame, routes: pd.DataFrame, origin: date, meta: dict) -> pd.DataFrame:
    """The frame the published model scores when given the target days' actual runs: built as of *origin*,
    from the history the 11:00 predict could see. Origin − 1's after-midnight runs (an hour past 24, or
    below the service day's cutover) were not synced by then, so they are left out."""
    hour = runs["hour"]
    late = (runs["service_date"] == pd.Timestamp(origin - timedelta(days=1))) & (
        (hour >= 24) | (hour < SERVICE_DAY_CUTOVER_HOURS)
    )
    return build_frame(
        with_route_ids(runs[~late], routes),
        origin,
        window_days=meta["window_days"],
        horizon_days=HORIZON_DAYS,
        clip_quantile=meta.get("clip_quantile"),
    )


def score_origin(
    card: Scorecard,
    origin: date,
    model_version: str,
    forecast: pd.DataFrame,
    frame: pd.DataFrame,
    preds: Predictions,
    point: np.ndarray,
    final_through: date,
    withheld_days: int,
) -> None:
    """Score one published forecast's target days through *final_through*. *frame*, *preds* and *point*
    are the counterfactual's: counterfactual_frame, then the published model's predictions and point."""
    final = (frame["service_date"] <= pd.Timestamp(final_through)).to_numpy()
    runs = frame.loc[final, [*KEY, "h", "delay_min", "b0", "slot_p10", "slot_p90"]].assign(
        cf=point[final], cf_q10=preds.q10[final], cf_q90=preds.q90[final]
    )
    published = forecast[[*KEY, "mean_min", "q10", "q90"]].rename(
        columns={"mean_min": "live", "q10": "live_q10", "q90": "live_q90"}
    )
    agencies = {int(agency) for agency in published["agency_id"].unique()}
    for agency_id in agencies:
        card.first_published[agency_id] = min(card.first_published.get(agency_id, origin), origin)
    matched = runs.merge(published, on=KEY, how="inner")
    for agency_id, count in runs[runs["agency_id"].isin(agencies)].groupby("agency_id").size().items():
        card.shares.setdefault(int(agency_id), Share()).actual_runs += int(count)
    for agency_id, count in matched.groupby("agency_id").size().items():
        card.shares.setdefault(int(agency_id), Share()).published_runs += int(count)

    paired = matched[matched["b0"].notna()]
    y = paired["delay_min"].to_numpy(dtype=float)
    _cover(card.intervals["live_slot"], y, paired["live_q10"], paired["live_q90"])
    _cover(card.intervals["counterfactual"], y, paired["cf_q10"], paired["cf_q90"])
    slot = paired.groupby(KEY, observed=True)
    _cover(card.intervals["counterfactual_slot"], y, slot["cf_q10"].transform("mean"), slot["cf_q90"].transform("mean"))
    has_b0 = (paired["slot_p10"].notna() & paired["slot_p90"].notna()).to_numpy()
    _cover(card.intervals["B0"], y[has_b0], paired["slot_p10"][has_b0], paired["slot_p90"][has_b0])

    cells = (
        paired.groupby([*KEY, "h"], observed=True)
        .agg(
            runs=("delay_min", "size"),
            actual=("delay_min", "mean"),
            cf=("cf", "mean"),
            b0=("b0", "first"),
            live=("live", "first"),
        )
        .reset_index()
    )
    this_origin = Bucket()
    for cell in cells.itertuples(index=False):
        live, counterfactual, b0, actual = float(cell.live), float(cell.cf), float(cell.b0), float(cell.actual)
        for bucket in (
            card.overall,
            card.by_lead.setdefault(int(cell.h), Bucket()),
            card.by_agency.setdefault(int(cell.agency_id), Bucket()),
            this_origin,
        ):
            bucket.add(live=live, counterfactual=counterfactual, b0=b0, actual=actual, runs=int(cell.runs))
        card.target_days.add(cell.service_date.date())
    card.origins.append(
        _drop_none(
            {
                "origin": origin.isoformat(),
                "model_version": model_version,
                "skill_live": _round(this_origin.skill("live")),
                "skill_counterfactual": _round(this_origin.skill("counterfactual")),
                "runs": int(this_origin.b0.weight),
                "withheld_days": withheld_days,
            }
        )
    )


def to_json(card: Scorecard, *, run_date: date, generated_at: datetime) -> dict:
    """The live.json document (schema 1). A bucket with no runs is left out, so every number is finite."""
    origins = sorted(card.origins, key=lambda row: row["origin"])
    window: dict = {"origins": len(origins), "target_days": len(card.target_days)}
    if origins:
        window = {"first_origin": origins[0]["origin"], "last_origin": origins[-1]["origin"], **window}
    overall = card.overall
    published = sum(share.published_runs for share in card.shares.values())
    actual = sum(share.actual_runs for share in card.shares.values())
    doc: dict = {
        "schema": SCHEMA,
        "run_date": run_date.isoformat(),
        "generated_at": generated_at.isoformat(),
        "window": window,
        "overall": {
            name: _errors(stats)
            for name, stats in (("live", overall.live), ("counterfactual", overall.counterfactual), ("B0", overall.b0))
            if stats.weight
        },
        "skill": _drop_none(
            {"live": _round(overall.skill("live")), "counterfactual": _round(overall.skill("counterfactual"))}
        ),
        "interval": {
            name: {"coverage": _round(stats.coverage), "mean_width": _round(stats.mean_width)}
            for name, stats in card.intervals.items()
            if stats.runs
        },
        "by_lead": [
            _drop_none(
                {
                    "lead": lead,
                    "skill_live": _round(bucket.skill("live")),
                    "skill_counterfactual": _round(bucket.skill("counterfactual")),
                    "runs": int(bucket.b0.weight),
                }
            )
            for lead, bucket in sorted(card.by_lead.items())
            if bucket.b0.weight
        ],
        "agencies": [_agency(card, agency_id) for agency_id in sorted(card.shares)],
        "origins": origins,
    }
    if actual:
        doc["published_share"] = _round(published / actual)
    return doc


def _agency(card: Scorecard, agency_id: int) -> dict:
    bucket, share = card.by_agency.get(agency_id, Bucket()), card.shares[agency_id]
    return _drop_none(
        {
            "agency_id": agency_id,
            "skill_live": _round(bucket.skill("live")),
            "skill_counterfactual": _round(bucket.skill("counterfactual")),
            "mae_live": _round(bucket.live.mae),
            "mae_counterfactual": _round(bucket.counterfactual.mae),
            "mae_b0": _round(bucket.b0.mae),
            "runs": int(bucket.b0.weight),
            "published_share": _round(share.published_runs / share.actual_runs) if share.actual_runs else None,
            "first_published_origin": (
                card.first_published[agency_id].isoformat() if agency_id in card.first_published else None
            ),
        }
    )


def _cover(stats: IntervalStats, y: np.ndarray, lo, hi) -> None:
    lo, hi = np.asarray(lo, dtype=float), np.asarray(hi, dtype=float)
    stats.runs += len(y)
    stats.covered += int(((y >= lo) & (y <= hi)).sum())
    stats.width += float((hi - lo).sum())


def _errors(stats: ErrorStats) -> dict:
    return {"mae": _round(stats.mae), "rmse": _round(stats.rmse), "runs": int(stats.weight)}


def _round(value: float | None) -> float | None:
    return None if value is None else round(float(value), 4)


def _drop_none(row: dict) -> dict:
    return {key: value for key, value in row.items() if value is not None}
