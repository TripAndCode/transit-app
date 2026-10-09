from datetime import date, timedelta

from ml.baselines import b0, b1, b2
from ml.cells import Cell, History

ORIGIN = date(2026, 9, 1)  # a Tuesday


def _cell(day: date, mean: float, runs: int = 1, hour: int = 8, route: str = "R1") -> Cell:
    return Cell(route, day, hour, runs, mean * runs)


def test_b0_pools_the_same_weekday_and_hour_over_the_window_by_runs():
    tuesdays = [ORIGIN - timedelta(days=7 * k) for k in (1, 2)]
    history = History(
        [_cell(tuesdays[0], 2.0, runs=3), _cell(tuesdays[1], 4.0, runs=1), _cell(ORIGIN - timedelta(days=1), 9.0)]
    )
    target = ORIGIN + timedelta(days=7)
    assert b0(history, "R1", target, 8, ORIGIN) == (2.0 * 3 + 4.0) / 4


def test_b0_ignores_cells_before_the_window_and_at_or_after_the_origin():
    history = History(
        [_cell(ORIGIN - timedelta(days=35), 50.0), _cell(ORIGIN, 50.0), _cell(ORIGIN - timedelta(days=7), 1.0)]
    )
    assert b0(history, "R1", ORIGIN + timedelta(days=7), 8, ORIGIN) == 1.0


def test_b1_takes_the_latest_same_weekday_cell_in_the_window():
    history = History([_cell(ORIGIN - timedelta(days=14), 5.0), _cell(ORIGIN - timedelta(days=7), 3.0)])
    assert b1(history, "R1", ORIGIN + timedelta(days=7), 8, ORIGIN) == 3.0


def test_b2_pools_the_route_over_every_hour_in_the_window():
    history = History([_cell(ORIGIN - timedelta(days=1), 1.0, hour=8), _cell(ORIGIN - timedelta(days=2), 3.0, hour=17)])
    assert b2(history, "R1", ORIGIN) == 2.0


def test_a_route_with_no_history_in_the_window_gets_no_prediction():
    history = History([_cell(ORIGIN - timedelta(days=7), 1.0, route="R1")])
    assert b0(history, "NEW", ORIGIN + timedelta(days=7), 8, ORIGIN) is None
    assert b1(history, "NEW", ORIGIN + timedelta(days=7), 8, ORIGIN) is None
    assert b2(history, "NEW", ORIGIN) is None


def test_no_baseline_can_see_data_from_the_origin_onwards():
    past = [_cell(ORIGIN - timedelta(days=7), 1.0), _cell(ORIGIN - timedelta(days=1), 2.0)]
    future = [_cell(ORIGIN + timedelta(days=d), 99.0) for d in range(0, 8)]
    before, after = History(past), History(past + future)
    target = ORIGIN + timedelta(days=7)
    assert b0(before, "R1", target, 8, ORIGIN) == b0(after, "R1", target, 8, ORIGIN)
    assert b1(before, "R1", target, 8, ORIGIN) == b1(after, "R1", target, 8, ORIGIN)
    assert b2(before, "R1", ORIGIN) == b2(after, "R1", ORIGIN)
