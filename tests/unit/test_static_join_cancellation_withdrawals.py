"""`static_join.parse_feed` stores a trip CANCELED without a listed stop as
one withdrawal per stop `cancellation_withdrawals` names, the rule offline
readers of the archives (scripts/validate_vp_predictions.py) share.

DB-free: a fake cursor answers the cancelled trip's timetable lookup.
"""

from datetime import datetime

import pytest

from pipeline.clickhouse import TRIP_CANCELED, TRIP_LEVEL_STOP_SEQUENCE
from pipeline.strategies import static_join
from tests.fixtures.gtfs_rt import trip_update, trip_update_feed

_STOPS = [(1, "A", "08:00:00"), (2, "B", "08:10:00"), (3, "C", "08:20:00"), (4, "D", "08:30:00")]


class _Cursor:
    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def execute(self, sql, _params):
        self.result = [("T1", "R1", "WKDY", "v1", *stop) for stop in _STOPS] if "LEFT JOIN" in sql else []

    def fetchall(self):
        return self.result


class _Conn:
    def cursor(self):
        return _Cursor()


@pytest.mark.parametrize("captured_at", ["2026-11-19T23:15:00+00:00", "2026-11-19T23:45:00+00:00"])
def test_parse_feed_withdraws_exactly_the_stops_cancellation_withdrawals_names(captured_at):
    """Captured at 08:15 JST, stops 3 and 4 are still to come; at 08:45 none
    is, and the trip-level row stands alone."""
    feed = trip_update_feed(trip_update("T1", schedule_relationship=TRIP_CANCELED))
    rows = static_join.parse_feed(feed, captured_at, "20261119/TripUpdate_231500.pb", 9, _Conn())
    withdrawn = [(row[6], row[8]) for row in rows if row[6] != TRIP_LEVEL_STOP_SEQUENCE]
    expected = static_join.cancellation_withdrawals(_STOPS, datetime.fromisoformat(captured_at))
    assert withdrawn == [(seq, stop_id) for seq, stop_id, _departure in expected]
    assert all(row[7] is None and row[10] == TRIP_CANCELED for row in rows)
    assert len(rows) == (len(expected) or 1)
    assert withdrawn == ([(3, "C"), (4, "D")] if captured_at.endswith("23:15:00+00:00") else [])
