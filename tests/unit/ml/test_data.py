"""The workbench fetches every service date a stop event of the agency's rows
can be filed under, a day either side of the service days its rows were
captured on (pipeline.service_day.filed_date_ch)."""

from datetime import date, timedelta
from itertools import pairwise

from ml.data import fetch_cells

FIRST = date(2026, 6, 1)
LAST = date(2026, 6, 9)


class _Client:
    """Captured service days FIRST through LAST; records each cells read's dates."""

    def __init__(self) -> None:
        self.reads: list[tuple[date, date]] = []

    def query(self, sql, parameters=None):
        class _Result:
            result_rows: list = []

        result = _Result()
        if "ORDER BY captured_at ASC" in sql:
            result.result_rows = [(FIRST,)]
        elif "ORDER BY captured_at DESC" in sql:
            result.result_rows = [(LAST,)]
        else:
            self.reads.append((parameters["start"], parameters["end"]))
        return result


def test_cells_are_read_from_the_day_before_the_first_capture_to_the_day_after_the_last():
    client = _Client()
    fetch_cells(client, 8, chunk_days=4)
    day = timedelta(days=1)
    assert client.reads[0][0] == FIRST - day
    assert client.reads[-1][1] == LAST + day
    assert all(later[0] == earlier[1] + day for earlier, later in pairwise(client.reads))


def test_a_start_day_still_bounds_the_read():
    client = _Client()
    fetch_cells(client, 8, since=date(2026, 6, 5))
    assert client.reads[0][0] == date(2026, 6, 5)
    assert client.reads[-1][1] == LAST + timedelta(days=1)
