"""analyze builds agg_route_segment_runtime: running time per timetable segment.

The timetable for trip T1 skips sequence 3, so its segments are S1→S2, S2→S4
and S4→S5, the last ending at the trip's terminus:

    seq  stop  arrival   departure
    1    S1    -         08:00:00
    2    S2    08:05:00  08:06:00
    4    S4    08:10:00  08:10:00
    5    S5    08:20:00  -
"""

from datetime import date, datetime, timedelta, timezone

import pytest

from pipeline.analyze import analyze
from pipeline.clickhouse import insert_updates
from tests.pipeline.test_analyze import _set_ingest_strategy

_JST = timezone(timedelta(hours=9))
_TIMETABLE = [(1, "S1", None, "08:00:00"), (2, "S2", "08:05:00", "08:06:00"), (4, "S4", "08:10:00", "08:10:00")]
_TIMETABLE.append((5, "S5", "08:20:00", None))


def _seed(pg_conn, agency_id, timetable=_TIMETABLE, trip_id="T1"):
    _set_ingest_strategy(pg_conn, agency_id, "static_join")
    with pg_conn.cursor() as cur:
        for _seq, stop, _arr, _dep in timetable:
            cur.execute(
                "INSERT INTO static_stops (agency_id, stop_id, stop_name) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                (agency_id, stop, stop),
            )
        for seq, stop, arr, dep in timetable:
            cur.execute(
                "INSERT INTO static_stop_times "
                "(agency_id, trip_id, stop_sequence, stop_id, arrival_time, departure_time) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (agency_id, trip_id, seq, stop, arr, dep),
            )
    pg_conn.commit()


def _visit(day: date, clock: str, seq: int, stop: str, *, dep=None, arr=None, dep_first=False, trip_id="T1"):
    """One stop visit's polls as a static_join feed sends them: the arrival 30 s
    before the departure, or after it when *dep_first* (the departure then is
    the prediction standing when the bus arrived). *clock* is the stop's
    scheduled time in JST on *day*."""
    hh, mm, ss = (int(part) for part in clock.split(":"))
    at = datetime(day.year, day.month, day.day, tzinfo=_JST) + timedelta(hours=hh, minutes=mm, seconds=ss)
    at = at.astimezone(timezone.utc)
    polls = [("arr", at - timedelta(seconds=-30 if dep_first else 30), None, arr), ("dep", at, dep, None)]
    return [
        (
            f"{kind}-{trip_id}-{day}-{seq}.pb",
            moment,
            trip_id,
            "平日",
            clock if hh < 24 else None,
            "R1",
            seq,
            dep_delay,
            stop,
            arr_delay,
            None,
            None,
            None,
        )
        for kind, moment, dep_delay, arr_delay in polls
        if dep_delay is not None or arr_delay is not None
    ]


def _segments(pg_conn, agency_id):
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT date, from_stop_id, to_stop_id, time_band, to_stop_sequence, run_sec, scheduled_samples, "
            "scheduled_sum_sec, hold_eligible, holds, terminal_arrivals, terminal_early "
            "FROM agg_route_segment_runtime WHERE agency_id = %s AND route_code = 'R1' "
            "ORDER BY date, to_stop_sequence",
            (agency_id,),
        )
        return cur.fetchall()


D1, D2, D3 = date(2026, 4, 1), date(2026, 4, 2), date(2026, 4, 3)


def test_each_segment_takes_the_run_from_its_own_first_stop(pg_conn, agency_id, ch_client):
    """Day 1: S1 leaves 30 s late (28830), S2 is reached 20 s late (29120) and
    left 50 s late (29210), S4 reached and left 10 s late (29410), and the
    terminus S5 reached 90 s early (29910). Runs: 290 s against 300 s
    scheduled, 200 s against 240 s (across the gap in sequence numbers), and
    500 s against 600 s. The terminus arrival is early (at least 60 s)."""
    _seed(pg_conn, agency_id)
    insert_updates(
        ch_client,
        agency_id,
        [
            *_visit(D1, "08:00:00", 1, "S1", dep=30),
            *_visit(D1, "08:06:00", 2, "S2", arr=20, dep=50),
            *_visit(D1, "08:10:00", 4, "S4", arr=10, dep=10),
            *_visit(D1, "08:20:00", 5, "S5", arr=-90),
        ],
    )
    analyze(agency_id, pg_conn, ch_client)

    assert _segments(pg_conn, agency_id) == [
        (D1, "S1", "S2", "morning", 2, [290], 1, 300, 0, 0, 0, 0),
        (D1, "S2", "S4", "morning", 4, [200], 1, 240, 0, 0, 0, 0),
        (D1, "S4", "S5", "morning", 5, [500], 1, 600, 0, 0, 1, 1),
    ]


def test_a_hold_is_judged_at_the_first_stop_reached_on_time_or_early(pg_conn, agency_id, ch_client):
    """S2 is reached 30 s early (29070) and left 120 s late (29280): a 210 s
    dwell against 60 s scheduled, 150 s beyond it, so a hold on S2→S4. S4 is
    reached on time and left on time: eligible, and no hold. S1 has no arrival
    to judge."""
    _seed(pg_conn, agency_id)
    insert_updates(
        ch_client,
        agency_id,
        [
            *_visit(D2, "08:00:00", 1, "S1", dep=0),
            *_visit(D2, "08:06:00", 2, "S2", arr=-30, dep=120),
            *_visit(D2, "08:10:00", 4, "S4", arr=0, dep=0),
            *_visit(D2, "08:20:00", 5, "S5", arr=0),
        ],
    )
    analyze(agency_id, pg_conn, ch_client)

    assert _segments(pg_conn, agency_id) == [
        (D2, "S1", "S2", "morning", 2, [270], 1, 300, 0, 0, 0, 0),
        (D2, "S2", "S4", "morning", 4, [120], 1, 240, 1, 1, 0, 0),
        (D2, "S4", "S5", "morning", 5, [600], 1, 600, 1, 0, 1, 0),
    ]


def test_no_hold_is_counted_from_a_departure_reported_before_the_arrival(pg_conn, agency_id, ch_client):
    """The same 150 s excess dwell at S2, but its only departure poll precedes
    the arrival: a prediction, not a departure anyone saw. The run from S2 still counts."""
    _seed(pg_conn, agency_id)
    insert_updates(
        ch_client,
        agency_id,
        [
            *_visit(D2, "08:00:00", 1, "S1", dep=0),
            *_visit(D2, "08:06:00", 2, "S2", arr=-30, dep=120, dep_first=True),
            *_visit(D2, "08:10:00", 4, "S4", arr=0, dep=0),
        ],
    )
    analyze(agency_id, pg_conn, ch_client)

    rows = {(r[1], r[2]): r for r in _segments(pg_conn, agency_id)}
    assert rows[("S2", "S4")][5] == [120]
    assert rows[("S2", "S4")][8:10] == (0, 0)


def test_a_stop_the_feed_did_not_report_leaves_the_next_segment_without_a_run(pg_conn, agency_id, ch_client):
    """S4 is not reported, so S2's departure to S5's arrival spans two segments
    and is no run of S4→S5. The terminus arrival still counts, and S2→S4 has
    no row, having no visit to end it."""
    _seed(pg_conn, agency_id)
    insert_updates(
        ch_client,
        agency_id,
        [
            *_visit(D3, "08:00:00", 1, "S1", dep=0),
            *_visit(D3, "08:06:00", 2, "S2", arr=0, dep=0),
            *_visit(D3, "08:20:00", 5, "S5", arr=30),
        ],
    )
    analyze(agency_id, pg_conn, ch_client)

    assert _segments(pg_conn, agency_id) == [
        (D3, "S1", "S2", "morning", 2, [300], 1, 300, 0, 0, 0, 0),
        (D3, "S4", "S5", "morning", 5, [], 0, 0, 0, 0, 1, 0),
    ]


@pytest.mark.parametrize(("arrival", "departure"), [("00:03:00", "00:04:00"), ("24:03:00", "24:04:00")])
def test_a_segment_runs_forward_across_midnight_and_takes_its_departures_band(
    pg_conn, agency_id, ch_client, arrival, departure
):
    """Leaving at 23:58 and calling at 00:03, written as a plain clock or as
    GTFS's 24:03, is a 5 minute run in the night band. The segment leaving at
    00:04 is in the late-night band of the same service day."""
    _seed(
        pg_conn,
        agency_id,
        [(1, "S1", None, "23:58:00"), (2, "S2", arrival, departure), (3, "S3", "00:10:00", None)],
    )
    insert_updates(
        ch_client,
        agency_id,
        [
            *_visit(D1, "23:58:00", 1, "S1", dep=0),
            *_visit(D1, "24:04:00", 2, "S2", arr=0, dep=0),
            *_visit(D1, "24:10:00", 3, "S3", arr=0),
        ],
    )
    analyze(agency_id, pg_conn, ch_client)

    assert [(r[0], r[1], r[2], r[3], r[5], r[7]) for r in _segments(pg_conn, agency_id)] == [
        (D1, "S1", "S2", "night", [300], 300),
        (D1, "S2", "S3", "late_night", [360], 360),
    ]


def test_trips_sharing_a_stop_pair_share_its_row(pg_conn, agency_id, ch_client):
    """A second trip T2 calls at S2 then S4 under other sequence numbers: one
    segment, two runs, sorted. T1's unreported S1 still names S1→S2, with no
    run; T2 starts at S2, so no segment ends there for it."""
    _seed(pg_conn, agency_id)
    _seed(pg_conn, agency_id, [(7, "S2", "08:30:00", "08:31:00"), (8, "S4", "08:35:00", None)], trip_id="T2")
    insert_updates(
        ch_client,
        agency_id,
        [
            *_visit(D1, "08:06:00", 2, "S2", arr=0, dep=0),
            *_visit(D1, "08:10:00", 4, "S4", arr=0),
            *_visit(D1, "08:31:00", 7, "S2", arr=0, dep=0, trip_id="T2"),
            *_visit(D1, "08:35:00", 8, "S4", arr=-60, trip_id="T2"),
        ],
    )
    analyze(agency_id, pg_conn, ch_client)

    assert _segments(pg_conn, agency_id) == [
        (D1, "S1", "S2", "morning", 2, [], 0, 0, 0, 0, 0, 0),
        (D1, "S2", "S4", "morning", 4, [180, 240], 2, 480, 2, 0, 1, 1),
    ]


def _rows_by_date(pg_conn, agency_id, table):
    with pg_conn.cursor() as cur:
        cur.execute(
            f"SELECT date, COUNT(*) FROM {table} WHERE agency_id = %s GROUP BY date ORDER BY date", (agency_id,)
        )
        return cur.fetchall()


def test_a_table_new_to_the_agency_rebuilds_the_visits_tables_together(pg_conn, agency_id, ch_client):
    """The segment table is new while the dwell table has one changed date:
    both read one load of the visits, so both rebuild every date, and neither
    is left empty nor written twice."""
    _seed(pg_conn, agency_id)
    insert_updates(
        ch_client, agency_id, [*_visit(D1, "08:06:00", 2, "S2", arr=0, dep=0), *_visit(D1, "08:10:00", 4, "S4", arr=0)]
    )
    analyze(agency_id, pg_conn, ch_client)
    with pg_conn.cursor() as cur:
        cur.execute(
            "DELETE FROM agg_table_state WHERE agency_id = %s AND table_name = 'agg_route_segment_runtime'",
            (agency_id,),
        )
        cur.execute("DELETE FROM agg_route_segment_runtime WHERE agency_id = %s", (agency_id,))
    pg_conn.commit()
    insert_updates(
        ch_client, agency_id, [*_visit(D2, "08:06:00", 2, "S2", arr=0, dep=0), *_visit(D2, "08:10:00", 4, "S4", arr=0)]
    )

    analyze(agency_id, pg_conn, ch_client)

    assert _rows_by_date(pg_conn, agency_id, "agg_route_segment_runtime") == [(D1, 2), (D2, 2)]
    assert _rows_by_date(pg_conn, agency_id, "agg_route_daily_dwell_run") == [(D1, 1), (D2, 1)]
