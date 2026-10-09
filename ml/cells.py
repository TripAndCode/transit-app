"""Route×date×hour cells: the grain the baselines predict and the evaluation scores."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True, slots=True)
class Cell:
    route_code: str
    service_date: date
    hour: int
    runs: int
    delay_sum_min: float

    @property
    def mean_min(self) -> float:
        return self.delay_sum_min / self.runs
