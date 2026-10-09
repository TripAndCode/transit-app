"""Error accumulators weighted by runs, so a cell counts as often as the trips it holds."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ErrorStats:
    weight: float = 0.0
    abs_err: float = 0.0
    sq_err: float = 0.0

    def add(self, error: float, weight: float) -> None:
        self.weight += weight
        self.abs_err += abs(error) * weight
        self.sq_err += error * error * weight

    def merge(self, other: ErrorStats) -> None:
        self.weight += other.weight
        self.abs_err += other.abs_err
        self.sq_err += other.sq_err

    @property
    def mae(self) -> float | None:
        return self.abs_err / self.weight if self.weight else None

    @property
    def rmse(self) -> float | None:
        return (self.sq_err / self.weight) ** 0.5 if self.weight else None
