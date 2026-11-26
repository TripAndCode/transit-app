"""agg_stop_departures_daily keeps each stop's departures of a service day as
(scheduled time on the service day's clock, delay) pairs in time order: a
departure written at face value before the 04:00 cutover is the same night's,
a day later on the clock, as one stored only in its extended `scheduled_sec`
is. Its departures are the ones agg_stop_hour_daily counts, hour by hour.
"""

from collections import Counter
from datetime import date, datetime

from pipeline.analyze import analyze
from pipeline.clickhouse import insert_updates
from tests.pipeline.ch_agg_helpers import seed_schedule, update_row

DAY = date(2026, 9, 14)  # a Monday
_TIED_DELAYS = (301, -60, 60, -61, 300, 61)
# (trip, captured JST, scheduled_time, scheduled_sec, delay)
_DEPARTURES = [
    *((f"tied{i}", datetime(2026, 9, 14, 8, 15), "08:10:00", None, delay) for i, delay in enumerate(_TIED_DELAYS)),
    ("early", datetime(2026, 9, 14, 7, 5), "07:00:00", None, 120),
    ("face", datetime(2026, 9, 15, 0, 45), "00:40:00", None, 90),
    ("extended", datetime(2026, 9, 15, 0, 45), None, 24 * 3600 + 40 * 60, 30),
    ("last", datetime(2026, 9, 15, 3, 59), "03:59:00", None, -30),
    ("first", datetime(2026, 9, 15, 4, 5), "04:00:00", None, 0),
    ("unscheduled", datetime(2026, 9, 14, 9, 0), None, None, 45),
]
_08_10 = 8 * 3600 + 600
_00_40 = 24 * 3600 + 40 * 60


def test_each_days_departures_are_kept_in_time_order_on_the_service_days_clock(pg_conn, agency_id, ch_client):
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
            "SELECT stop_id, date, scheduled_secs, dep_delays FROM agg_stop_departures_daily WHERE agency_id = %s",
            (agency_id,),
        )
        rows = {(stop, d): (secs, delays) for stop, d, secs, delays in cur.fetchall()}
        cur.execute(
            "SELECT date, hour, samples, sum_delay_sec FROM agg_stop_hour_daily "
            "WHERE agency_id = %s AND stop_id = 'S1'",
            (agency_id,),
        )
        hours = {(d, hour): (samples, delay_sum) for d, hour, samples, delay_sum in cur.fetchall()}

    # Ties in time go by delay. A departure with no scheduled time is left out.
    assert rows == {
        ("S1", DAY): (
            [7 * 3600, *[_08_10] * 6, _00_40, _00_40, 27 * 3600 + 59 * 60],
            [120, *sorted(_TIED_DELAYS), 30, 90, -30],
        ),
        ("S1", date(2026, 9, 15)): ([4 * 3600], [0]),
    }
    filed: Counter = Counter()
    delay_sums: Counter = Counter()
    for (_, d), (secs, delays) in rows.items():
        for sec, delay in zip(secs, delays, strict=True):
            filed[d, sec // 3600] += 1
            delay_sums[d, sec // 3600] += delay
    assert {key: (filed[key], delay_sums[key]) for key in filed} == hours
