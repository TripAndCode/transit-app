"""The three baselines a model has to beat (spec §6). B0 is the app's own
"expected delay": the same route×weekday×hour, pooled by runs, over the 28
days before the origin."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta

from ml.cells import Cell, History

WINDOW_DAYS = 28


def _window(origin: date, window_days: int) -> tuple[date, date]:
    return origin - timedelta(days=window_days), origin - timedelta(days=1)


def _pooled(cells: Sequence[Cell]) -> float | None:
    runs = sum(c.runs for c in cells)
    return sum(c.delay_sum_min for c in cells) / runs if runs else None


def b0(
    history: History, route: str, target: date, hour: int, origin: date, window_days: int = WINDOW_DAYS
) -> float | None:
    start, end = _window(origin, window_days)
    return _pooled(history.slot_window(route, target.isoweekday(), hour, start, end))


def b1(
    history: History, route: str, target: date, hour: int, origin: date, window_days: int = WINDOW_DAYS
) -> float | None:
    start, end = _window(origin, window_days)
    cells = history.slot_window(route, target.isoweekday(), hour, start, end)
    return cells[-1].mean_min if cells else None


def b2(history: History, route: str, origin: date, window_days: int = WINDOW_DAYS) -> float | None:
    start, end = _window(origin, window_days)
    return _pooled(history.route_window(route, start, end))
