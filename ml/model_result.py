"""A model backtest's result: per-agency slice rows for B0 and the model, run-level
interval figures, and per-origin skill. Plain data, readable without the `ml`
dependency group, so the report renders it anywhere."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

from ml.backtest import AgencyResult, results_from_json, results_to_json


@dataclass
class IntervalStats:
    runs: int = 0
    covered: int = 0
    width: float = 0.0
    pinball: dict[str, float] = field(default_factory=dict)

    def merge(self, other: IntervalStats) -> None:
        self.runs += other.runs
        self.covered += other.covered
        self.width += other.width
        for alpha, total in other.pinball.items():
            self.pinball[alpha] = self.pinball.get(alpha, 0.0) + total

    @property
    def coverage(self) -> float | None:
        return self.covered / self.runs if self.runs else None

    @property
    def mean_width(self) -> float | None:
        return self.width / self.runs if self.runs else None

    def mean_pinball(self, alpha: str) -> float | None:
        return self.pinball[alpha] / self.runs if self.runs and alpha in self.pinball else None


@dataclass
class ModelBacktestResult:
    params: dict[str, Any]
    origins: list[date]
    cutoffs: list[date]
    agencies: list[AgencyResult]
    intervals: dict[str, dict[str, IntervalStats]]
    origin_skill: list[tuple[date, float | None]]
    fallback_agencies: list[int] = field(default_factory=list)


def merged_intervals(result: ModelBacktestResult, method: str) -> IntervalStats:
    total = IntervalStats()
    for stats in result.intervals.get(method, {}).values():
        total.merge(stats)
    return total


def result_to_json(result: ModelBacktestResult) -> str:
    return json.dumps(
        {
            "params": result.params,
            "origins": [d.isoformat() for d in result.origins],
            "cutoffs": [d.isoformat() for d in result.cutoffs],
            "agencies": json.loads(results_to_json(result.agencies)),
            "intervals": {m: {a: asdict(s) for a, s in by.items()} for m, by in result.intervals.items()},
            "origin_skill": [[d.isoformat(), s] for d, s in result.origin_skill],
            "fallback_agencies": result.fallback_agencies,
        },
        indent=1,
    )


def result_from_json(text: str) -> ModelBacktestResult:
    raw = json.loads(text)
    return ModelBacktestResult(
        params=raw["params"],
        origins=[date.fromisoformat(d) for d in raw["origins"]],
        cutoffs=[date.fromisoformat(d) for d in raw["cutoffs"]],
        agencies=results_from_json(json.dumps(raw["agencies"])),
        intervals={m: {a: IntervalStats(**s) for a, s in by.items()} for m, by in raw["intervals"].items()},
        origin_skill=[(date.fromisoformat(d), s) for d, s in raw["origin_skill"]],
        fallback_agencies=raw.get("fallback_agencies", []),
    )
