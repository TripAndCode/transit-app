"""agg_stop_hour_daily files each observed departure under its stop, its service
date and its hour on the service day's clock, the hours the planned departures
beside it are counted in (pipeline.timetable_sampling): a departure written at
face value before the 04:00 cutover is the same night's hour 24 to 27, as one
stored only in its extended `scheduled_sec` is. Its counts read the default
on-time window exactly through pipeline.histogram.on_time_from_columns.
"""

from datetime import date, datetime

from pipeline.analyze import analyze
from pipeline.clickhouse import insert_updates
from pipeline.histogram import on_time_from_columns
from pipeline.reports.rankings import DEFAULT_EARLY_TOLERANCE_SEC, DEFAULT_LATE_TOLERANCE_SEC
from tests.pipeline.ch_agg_helpers import seed_schedule, update_row

DAY = date(2026, 9, 14)  # a Monday
_EDGE_DELAYS = (-61, -60, 60, 61, 300, 301)
# (trip, captured JST, scheduled_time, scheduled_sec, delay)
_DEPARTURES = [
    *((f"edge{i}", datetime(2026, 9, 14, 8, 15), "08:10:00", None, delay) for i, delay in enumerate(_EDGE_DELAYS)),
    ("face", datetime(2026, 9, 15, 0, 45), "00:40:00", None, 30),
    ("extended", datetime(2026, 9, 15, 0, 45), None, 24 * 3600 + 40 * 60, 90),
    ("last", datetime(2026, 9, 15, 3, 59), "03:59:00", None, -30),
    ("first", datetime(2026, 9, 15, 4, 5), "04:00:00", None, 0),
]


def test_each_departure_is_filed_under_its_service_days_hour(pg_conn, agency_id, ch_client):
    seed_schedule(pg_conn, agency_id, [trip for trip, *_ in _DEPARTURES])
    insert_updates(
        ch_client,
        agency_id,
        [
            update_row(captured, trip, delay, scheduled_time=clock, scheduled_sec=sec)
            for trip, captured, clock, sec, delay in _DEPARTURES
        ],
    )
    analyze(agency_id, pg_conn, ch_client)
    pg_conn.commit()

    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT stop_id, date, hour, samples, sum_delay_sec, on_time_count, late5_count, early_count "
            "FROM agg_stop_hour_daily WHERE agency_id = %s",
            (agency_id,),
        )
        rows = {(stop, d, hour): tuple(counts) for stop, d, hour, *counts in cur.fetchall()}

    assert rows == {
        # At most a minute late, early ones included: -61, -60 and 60. Over
        # five minutes late: 301. Over the early edge: -61.
        ("S1", DAY, 8): (6, sum(_EDGE_DELAYS), 3, 1, 1),
        ("S1", DAY, 24): (2, 120, 1, 0, 0),
        ("S1", DAY, 27): (1, -30, 1, 0, 0),
        ("S1", date(2026, 9, 15), 4): (1, 0, 1, 0, 0),
    }
    samples, _, on_time_count, late5_count, early_count = rows["S1", DAY, 8]
    on_time = on_time_from_columns(
        DEFAULT_EARLY_TOLERANCE_SEC,
        DEFAULT_LATE_TOLERANCE_SEC,
        samples=samples,
        on_time_count=on_time_count,
        late5_count=late5_count,
        early_count=early_count,
    )
    assert on_time == sum(
        1 for delay in _EDGE_DELAYS if -DEFAULT_EARLY_TOLERANCE_SEC <= delay <= DEFAULT_LATE_TOLERANCE_SEC
    )
