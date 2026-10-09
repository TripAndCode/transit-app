"""Rolling-origin evaluation (spec §8). A forecast made on day T sees data
through T−1 and is scored on T+1..T+7. Every method is scored on the cells it
predicts; its skill against B0 only on the cells both predict, so a method
that abstains on hard cells cannot look better for it."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Any

from ml.baselines import WINDOW_DAYS, b0, b1, b2
from ml.cells import Cell, History
from ml.metrics import ErrorStats

METHODS = ("B0", "B1", "B2")
HORIZONS = range(1, 8)
PEAK_HOURS = frozenset({7, 8, 17, 18})
# A route-hour with this many trips or fewer averages one or two observations,
# so its error is mostly those trips' own noise; the report shows it apart.
SPARSE_RUNS = 2


@dataclass
class SliceRow:
    method: str
    horizon: int
    peak: bool
    sparse: bool
    target_runs: int = 0
    predicted_runs: int = 0
    errors: ErrorStats = field(default_factory=ErrorStats)
    paired: ErrorStats = field(default_factory=ErrorStats)
    paired_b0: ErrorStats = field(default_factory=ErrorStats)


@dataclass
class AgencyResult:
    agency_id: int
    first_day: date
    last_day: date
    days_of_data: int
    origins: list[date]
    short_history: bool
    rows: list[SliceRow]


def choose_origins(dates: Iterable[date], count: int, horizon: int = 7) -> list[date]:
    """The latest `count` days T with data on T−1 and on at least one of T+1..T+horizon."""
    have = set(dates)
    candidates = sorted({d + timedelta(days=1) for d in have})
    valid = [t for t in candidates if any(t + timedelta(days=h) in have for h in range(1, horizon + 1))]
    return valid[-count:]


def evaluate_agency(
    agency_id: int, cells: Sequence[Cell], *, origin_count: int = 28, window_days: int = WINDOW_DAYS
) -> AgencyResult:
    if not cells:
        raise ValueError(f"agency {agency_id} has no cells to evaluate")
    history = History(cells)
    by_date: dict[date, list[Cell]] = defaultdict(list)
    for cell in cells:
        by_date[cell.service_date].append(cell)
    dates = sorted(by_date)
    origins = choose_origins(dates, origin_count)
    rows: dict[tuple[str, int, bool, bool], SliceRow] = {}
    for origin in origins:
        # B2 depends only on the route and the origin; compute it once per pair.
        route_means: dict[str, float | None] = {}
        for horizon in HORIZONS:
            for cell in by_date.get(origin + timedelta(days=horizon), ()):
                route = cell.route_code
                if route not in route_means:
                    route_means[route] = b2(history, route, origin, window_days)
                predictions = {
                    "B0": b0(history, route, cell.service_date, cell.hour, origin, window_days),
                    "B1": b1(history, route, cell.service_date, cell.hour, origin, window_days),
                    "B2": route_means[route],
                }
                reference = predictions["B0"]
                key = (horizon, cell.hour in PEAK_HOURS, cell.runs <= SPARSE_RUNS)
                for method, prediction in predictions.items():
                    row = rows.setdefault((method, *key), SliceRow(method, *key))
                    row.target_runs += cell.runs
                    if prediction is None:
                        continue
                    row.predicted_runs += cell.runs
                    row.errors.add(prediction - cell.mean_min, cell.runs)
                    if reference is not None:
                        row.paired.add(prediction - cell.mean_min, cell.runs)
                        row.paired_b0.add(reference - cell.mean_min, cell.runs)
    return AgencyResult(
        agency_id=agency_id,
        first_day=dates[0],
        last_day=dates[-1],
        days_of_data=len(dates),
        origins=origins,
        short_history=len(dates) < window_days + 7,
        rows=[rows[k] for k in sorted(rows)],
    )


def results_to_json(results: Sequence[AgencyResult]) -> str:
    return json.dumps([asdict(r) for r in results], default=str, indent=1)


def _row_from_json(raw: dict[str, Any]) -> SliceRow:
    fields = dict(raw)
    for name in ("errors", "paired", "paired_b0"):
        fields[name] = ErrorStats(**raw[name])
    return SliceRow(**fields)


def results_from_json(text: str) -> list[AgencyResult]:
    return [
        AgencyResult(
            agency_id=raw["agency_id"],
            first_day=date.fromisoformat(raw["first_day"]),
            last_day=date.fromisoformat(raw["last_day"]),
            days_of_data=raw["days_of_data"],
            origins=[date.fromisoformat(d) for d in raw["origins"]],
            short_history=raw["short_history"],
            rows=[_row_from_json(r) for r in raw["rows"]],
        )
        for raw in json.loads(text)
    ]
