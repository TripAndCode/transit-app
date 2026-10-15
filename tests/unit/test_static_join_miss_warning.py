"""`static_join.parse_feed` warns when most of a poll's stop updates find no
row in the loaded timetable. A timetable that is loaded but wrong (stale, or
keyed differently from the realtime feed) passes the ingest guard, so this
line is the only sign that rows are being stored with no schedule.

DB-free: a fake cursor answers the two timetable lookups.
"""

import logging

import pytest

from pipeline.strategies import static_join
from tests.fixtures.gtfs_rt import stop_time_update, trip_update, trip_update_feed


class _Cursor:
    def __init__(self, scheduled):
        self.scheduled = scheduled
        self.result = []

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def execute(self, sql, params):
        if "static_stop_times" in sql:
            trip_ids, seqs, _aid = params
            self.result = [(t, s, "08:00:00") for t, s in zip(trip_ids, seqs, strict=True) if (t, s) in self.scheduled]
        else:
            _aid, trip_ids = params
            self.result = [(t, "WKDY", "v1") for t in trip_ids]

    def fetchall(self):
        return self.result


class _Conn:
    def __init__(self, scheduled):
        self.scheduled = scheduled

    def cursor(self):
        return _Cursor(self.scheduled)


def _poll(n_stops):
    return trip_update_feed(
        trip_update("T1", *(stop_time_update(seq, departure_delay=0) for seq in range(1, n_stops + 1)), route_id="R1")
    )


def _warnings(caplog):
    return [r for r in caplog.records if r.name == static_join.__name__ and r.levelno >= logging.WARNING]


def _parse(caplog, n_stops, scheduled):
    with caplog.at_level(logging.INFO, logger=static_join.__name__):
        return static_join.parse_feed(_poll(n_stops), "2026-10-01T00:00:00+00:00", "oracle/f.pb", 8, _Conn(scheduled))


def test_a_poll_that_mostly_misses_the_timetable_warns_once_with_counts(caplog):
    n = static_join._MISS_WARN_MIN_ROWS
    scheduled = {("T1", 1)}

    rows = _parse(caplog, n, scheduled)

    assert len(rows) == n
    [warning] = _warnings(caplog)
    assert f"{n - 1}/{n}" in warning.getMessage()
    assert "oracle/f.pb" in warning.getMessage()


def test_a_poll_that_matches_the_timetable_stays_quiet(caplog):
    n = static_join._MISS_WARN_MIN_ROWS
    _parse(caplog, n, {("T1", seq) for seq in range(1, n + 1)})

    assert _warnings(caplog) == []


def test_a_minority_of_misses_is_logged_below_warning(caplog):
    n = static_join._MISS_WARN_MIN_ROWS
    _parse(caplog, n, {("T1", seq) for seq in range(2, n + 1)})

    assert _warnings(caplog) == []
    assert any("missed" in r.getMessage() and r.levelno == logging.INFO for r in caplog.records)


@pytest.mark.parametrize("n_stops", [1, 2])
def test_a_small_poll_that_misses_is_not_a_warning(caplog, n_stops):
    """A late-night poll of a trip or two not in the timetable is no sign the
    timetable is wrong."""
    assert n_stops < static_join._MISS_WARN_MIN_ROWS
    _parse(caplog, n_stops, set())

    assert _warnings(caplog) == []
