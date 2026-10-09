"""Route×date×hour cells: the grain the baselines predict and the evaluation scores."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import defaultdict
from collections.abc import Iterable
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


_Index = tuple[list[date], list[Cell]]
_EMPTY: _Index = ([], [])


def _by_date(cells: list[Cell]) -> _Index:
    ordered = sorted(cells, key=lambda c: c.service_date)
    return [c.service_date for c in ordered], ordered


class History:
    """Cells indexed by route×weekday×hour and by route, each sorted by date,
    so a baseline's window is two binary searches. Callers bound every window
    by an origin; nothing here knows about one."""

    def __init__(self, cells: Iterable[Cell]) -> None:
        slots: dict[tuple[str, int, int], list[Cell]] = defaultdict(list)
        routes: dict[str, list[Cell]] = defaultdict(list)
        for cell in cells:
            slots[(cell.route_code, cell.service_date.isoweekday(), cell.hour)].append(cell)
            routes[cell.route_code].append(cell)
        self._slots = {k: _by_date(v) for k, v in slots.items()}
        self._routes = {k: _by_date(v) for k, v in routes.items()}

    @staticmethod
    def _between(index: _Index, start: date, end: date) -> list[Cell]:
        dates, cells = index
        return cells[bisect_left(dates, start) : bisect_right(dates, end)]

    def slot_window(self, route: str, weekday: int, hour: int, start: date, end: date) -> list[Cell]:
        return self._between(self._slots.get((route, weekday, hour), _EMPTY), start, end)

    def route_window(self, route: str, start: date, end: date) -> list[Cell]:
        return self._between(self._routes.get(route, _EMPTY), start, end)
