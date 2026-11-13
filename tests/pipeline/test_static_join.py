"""static_join strategy tests: repeated-call regression + per-op fixture integration."""

import pathlib

import pytest

from pipeline.static_loader import load_static
from pipeline.strategies import static_join
from tests.fixtures.gtfs_rt import stop_time_update, trip_update, trip_update_feed

FIX = pathlib.Path(__file__).parent.parent / "fixtures"


def _hex_pb_with_one_trip(trip_id: str, route_id: str = "R1") -> bytes:
    """A FeedMessage with one TripUpdate for trip_id + route_id and one
    stop_time_update at stop_sequence=1 with a zero departure delay."""
    return trip_update_feed(trip_update(trip_id, stop_time_update(1, departure_delay=0), route_id=route_id))


def test_static_join_handles_repeated_calls_same_transaction(pg_conn):
    """Consecutive parse_feed calls inside one transaction on one connection
    must not collide or error, as ingest.py's batch-commit cadence makes
    them."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agencies (agency_name, feed_url, ingest_strategy) "
            "VALUES (%s, %s, 'static_join') RETURNING agency_id",
            ("static_join_collision_test", "http://collision-test.example.com/feed.pb"),
        )
        aid = cur.fetchone()[0]
    pg_conn.commit()

    pb = _hex_pb_with_one_trip("uuid-A")
    # First call — temp table created
    rows1 = static_join.parse_feed(pb, "2026-05-09T12:00:00", "f1.bin", aid, pg_conn)
    # Second call within same transaction (no commit) — must NOT raise
    rows2 = static_join.parse_feed(pb, "2026-05-09T12:00:30", "f2.bin", aid, pg_conn)

    assert len(rows1) == 1
    assert len(rows2) == 1
    # JOIN misses for both (we never loaded any static for this agency)
    assert rows1[0][3] is None  # service_type NULL
    assert rows1[0][4] is None  # scheduled_time NULL
    assert rows1[0][5] == "R1"  # route_code from RT


def test_static_join_resolves_two_different_trips_on_one_connection(pg_conn):
    """Two parse_feed calls on the same connection must each resolve their
    own trip -- not just avoid raising (the repeated-calls test above), but
    return correct, distinct JOIN results, never the first call's."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agencies (agency_name, feed_url, ingest_strategy) "
            "VALUES (%s, %s, 'static_join') RETURNING agency_id",
            ("static_join_reuse_test", "http://reuse-test.example.com/feed.pb"),
        )
        aid = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO static_trips (agency_id, trip_id, route_id, service_id) VALUES "
            "(%s, 'uuid-A', 'R1', '平日'), (%s, 'uuid-B', 'R2', '土日祝')",
            (aid, aid),
        )
        cur.execute(
            "INSERT INTO static_stop_times (agency_id, trip_id, stop_sequence, stop_id, departure_time) VALUES "
            "(%s, 'uuid-A', 1, 'S1', '07:05:00'), (%s, 'uuid-B', 1, 'S2', '08:10:00')",
            (aid, aid),
        )
    pg_conn.commit()

    pb_a = _hex_pb_with_one_trip("uuid-A", route_id="R1")
    pb_b = _hex_pb_with_one_trip("uuid-B", route_id="R2")

    rows_a = static_join.parse_feed(pb_a, "2026-05-09T12:00:00", "fa.bin", aid, pg_conn)
    rows_b = static_join.parse_feed(pb_b, "2026-05-09T12:00:30", "fb.bin", aid, pg_conn)

    assert len(rows_a) == 1 and len(rows_b) == 1
    assert rows_a[0][3] == "平日" and rows_a[0][4] == "07:05:00"
    assert rows_b[0][3] == "土日祝" and rows_b[0][4] == "08:10:00"


def test_static_join_isolates_results_across_agencies_sharing_one_connection(pg_conn):
    """Two different agencies' parse_feed calls on the SAME connection must
    each join their own schedule. cmd_ingest_live's all-agencies branch
    (gtfs_pipeline.py) and the production cron path (api/routers/internal.py)
    both loop `ingest_live(aid, conn, ...)` over every active agency using
    ONE psycopg2 connection. Both agencies below deliberately use the SAME
    trip_id so that cross-agency contamination would produce a
    plausible-looking (but wrong) match rather than an obvious miss.
    """

    def make_agency_with_trip(name, trip_id, service_id, departure_time):
        with pg_conn.cursor() as cur:
            cur.execute(
                "INSERT INTO agencies (agency_name, feed_url, ingest_strategy) "
                "VALUES (%s, %s, 'static_join') RETURNING agency_id",
                (name, f"http://{name}.example.com/feed.pb"),
            )
            aid = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO static_trips (agency_id, trip_id, route_id, service_id) VALUES (%s, %s, 'R1', %s)",
                (aid, trip_id, service_id),
            )
            cur.execute(
                "INSERT INTO static_stop_times (agency_id, trip_id, stop_sequence, stop_id, departure_time) "
                "VALUES (%s, %s, 1, 'S1', %s)",
                (aid, trip_id, departure_time),
            )
        return aid

    aid1 = make_agency_with_trip("cross_agency_test_1", "uuid-shared", "平日", "07:05:00")
    aid2 = make_agency_with_trip("cross_agency_test_2", "uuid-shared", "土日祝", "09:20:00")
    pg_conn.commit()

    pb = _hex_pb_with_one_trip("uuid-shared", route_id="R1")

    rows_1 = static_join.parse_feed(pb, "2026-05-09T12:00:00", "f1.bin", aid1, pg_conn)
    rows_2 = static_join.parse_feed(pb, "2026-05-09T12:00:30", "f2.bin", aid2, pg_conn)

    assert len(rows_1) == 1 and len(rows_2) == 1
    assert rows_1[0][3] == "平日" and rows_1[0][4] == "07:05:00"
    assert rows_2[0][3] == "土日祝" and rows_2[0][4] == "09:20:00"


def test_static_join_reads_the_schedule_as_it_stands_at_each_poll(pg_conn):
    """Nothing about the schedule is held between polls, so a reload between
    two of them on one connection shows in the second, and no copy of the
    schedule is left on the connection."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agencies (agency_name, feed_url, ingest_strategy) "
            "VALUES ('static_join_fresh', 'http://fresh.example.com/feed.pb', 'static_join') RETURNING agency_id"
        )
        aid = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO static_trips (agency_id, trip_id, route_id, service_id) VALUES (%s, 'T1', 'R1', 'WKDY')",
            (aid,),
        )
        cur.execute(
            "INSERT INTO static_stop_times (agency_id, trip_id, stop_sequence, stop_id, departure_time) "
            "VALUES (%s, 'T1', 1, 'S1', '08:00:00')",
            (aid,),
        )
    pg_conn.commit()
    pb = _hex_pb_with_one_trip("T1")

    first = static_join.parse_feed(pb, "2026-05-09T12:00:00", "f1.bin", aid, pg_conn)
    with pg_conn.cursor() as cur:
        cur.execute("UPDATE static_stop_times SET departure_time = '08:05:00' WHERE agency_id = %s", (aid,))
    pg_conn.commit()
    second = static_join.parse_feed(pb, "2026-05-09T12:00:30", "f2.bin", aid, pg_conn)

    assert (first[0][3], first[0][4]) == ("WKDY", "08:00:00")
    assert second[0][4] == "08:05:00"
    with pg_conn.cursor() as cur:
        # Scoped to this backend's own temp schema, not every relname in
        # pg_class matching the pattern -- the shared test Postgres can have
        # other backends' unrelated temp tables live at the same time.
        cur.execute(
            "SELECT count(*) FROM pg_class "
            "WHERE relpersistence = 't' AND relnamespace = pg_my_temp_schema() AND relname LIKE '_sj_schedule%%'"
        )
        assert cur.fetchone()[0] == 0


def test_static_join_zero_pads_single_digit_hour_scheduled_time(pg_conn):
    """GTFS's departure_time is raw, unpadded text — "7:05:00" is as valid
    as "07:05:00" per spec. ClickHouse stores scheduled_time as a plain
    String, which (unlike a Postgres TIME column) does not normalize this,
    and every hour-extraction read site downstream assumes a 2-digit hour.
    static_join must zero-pad at write time so a single-digit-hour
    departure_time doesn't sort into the wrong time band or crash toUInt8()
    reads."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agencies (agency_name, feed_url, ingest_strategy) "
            "VALUES (%s, %s, 'static_join') RETURNING agency_id",
            ("static_join_padding_test", "http://padding-test.example.com/feed.pb"),
        )
        aid = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO static_trips (agency_id, trip_id, route_id, service_id) VALUES (%s, %s, %s, %s)",
            (aid, "uuid-A", "R1", "平日"),
        )
        cur.execute(
            "INSERT INTO static_stop_times (agency_id, trip_id, stop_sequence, stop_id, departure_time) "
            "VALUES (%s, %s, %s, %s, %s)",
            (aid, "uuid-A", 1, "S1", "7:05:00"),
        )
    pg_conn.commit()

    pb = _hex_pb_with_one_trip("uuid-A")
    rows = static_join.parse_feed(pb, "2026-05-09T12:00:00", "f1.bin", aid, pg_conn)

    assert len(rows) == 1
    assert rows[0][4] == "07:05:00"


def test_static_join_nulls_scheduled_time_on_non_numeric_departure_time_hour(pg_conn):
    """departure_time is free-text GTFS data, not a validated column -- a
    non-numeric hour (garbage static data) must null that one row's
    scheduled_time rather than raise ValueError out of parse_feed (taking
    out the whole file's insert_updates batch) OR keep the raw garbage text
    (which analyze()'s `_analyze_visits.scheduled_time time` column can't
    cast, permanently failing that agency's analyze() once the row is
    durably stored in ClickHouse). NULL is already a first-class value
    here -- the row's dep_delay observation is kept, only the unusable
    schedule is dropped."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agencies (agency_name, feed_url, ingest_strategy) "
            "VALUES (%s, %s, 'static_join') RETURNING agency_id",
            ("static_join_bad_sched_test", "http://bad-sched-test.example.com/feed.pb"),
        )
        aid = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO static_trips (agency_id, trip_id, route_id, service_id) VALUES (%s, %s, %s, %s)",
            (aid, "uuid-A", "R1", "平日"),
        )
        cur.execute(
            "INSERT INTO static_stop_times (agency_id, trip_id, stop_sequence, stop_id, departure_time) "
            "VALUES (%s, %s, %s, %s, %s)",
            (aid, "uuid-A", 1, "S1", "ab:05:00"),
        )
    pg_conn.commit()

    pb = _hex_pb_with_one_trip("uuid-A")
    rows = static_join.parse_feed(pb, "2026-05-09T12:00:00", "f1.bin", aid, pg_conn)

    assert len(rows) == 1
    assert rows[0][4] is None
    assert rows[0][7] == 0  # dep_delay observation is still kept


def test_static_join_keeps_extended_hour_row_with_scheduled_sec(pg_conn):
    """GTFS allows departure_time like "25:30:00" for a trip continuing past
    midnight as the previous service day's schedule. scheduled_time
    (Nullable(String), read everywhere as same-day HH:MM[:SS]) still can't
    represent it, so that column stays NULL -- but the row itself must no
    longer be dropped: scheduled_sec holds the raw seconds-since-service-
    day-start value instead."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agencies (agency_name, feed_url, ingest_strategy) "
            "VALUES (%s, %s, 'static_join') RETURNING agency_id",
            ("static_join_extended_hour_test", "http://extended-hour-test.example.com/feed.pb"),
        )
        aid = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO static_trips (agency_id, trip_id, route_id, service_id) VALUES (%s, %s, %s, %s)",
            (aid, "uuid-A", "R1", "平日"),
        )
        cur.execute(
            "INSERT INTO static_stop_times (agency_id, trip_id, stop_sequence, stop_id, departure_time) "
            "VALUES (%s, %s, %s, %s, %s)",
            (aid, "uuid-A", 1, "S1", "25:30:00"),
        )
    pg_conn.commit()

    pb = _hex_pb_with_one_trip("uuid-A")
    rows = static_join.parse_feed(pb, "2026-05-09T12:00:00", "f1.bin", aid, pg_conn)

    assert len(rows) == 1  # kept, not dropped
    assert rows[0][4] is None  # scheduled_time still NULL (no same-day representation)
    assert rows[0][13] == 25 * 3600 + 30 * 60  # scheduled_sec == 91800


def test_static_join_populates_static_version_id_from_joined_static_trips(pg_conn):
    """static_version_id (the loaded static zip's filename stem, set on
    static_trips by pipeline.static_loader.load_static) must ride along with
    every RT row the JOIN resolves against that static_trips row -- NULL
    only when the JOIN itself misses, same as service_type/scheduled_time."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agencies (agency_name, feed_url, ingest_strategy) "
            "VALUES (%s, %s, 'static_join') RETURNING agency_id",
            ("static_join_version_id_test", "http://version-id-test.example.com/feed.pb"),
        )
        aid = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO static_trips (agency_id, trip_id, route_id, service_id, static_version_id) "
            "VALUES (%s, %s, %s, %s, %s)",
            (aid, "uuid-A", "R1", "平日", "gtfs_static_20260101"),
        )
        cur.execute(
            "INSERT INTO static_stop_times (agency_id, trip_id, stop_sequence, stop_id, departure_time) "
            "VALUES (%s, %s, %s, %s, %s)",
            (aid, "uuid-A", 1, "S1", "07:05:00"),
        )
    pg_conn.commit()

    pb = _hex_pb_with_one_trip("uuid-A")
    rows = static_join.parse_feed(pb, "2026-05-09T12:00:00", "f1.bin", aid, pg_conn)

    assert len(rows) == 1
    assert rows[0][14] == "gtfs_static_20260101"


def test_static_join_nulls_static_version_id_on_join_miss(pg_conn):
    """A trip_id with no matching static_trips row (JOIN miss) must leave
    static_version_id NULL, not fall back to some other agency's/trip's
    value."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agencies (agency_name, feed_url, ingest_strategy) "
            "VALUES (%s, %s, 'static_join') RETURNING agency_id",
            ("static_join_version_id_miss_test", "http://version-id-miss-test.example.com/feed.pb"),
        )
        aid = cur.fetchone()[0]
    pg_conn.commit()

    pb = _hex_pb_with_one_trip("uuid-unmatched")
    rows = static_join.parse_feed(pb, "2026-05-09T12:00:00", "f1.bin", aid, pg_conn)

    assert len(rows) == 1
    assert rows[0][14] is None


def test_static_join_nulls_scheduled_time_on_empty_departure_time(pg_conn):
    """An empty departure_time is legal GTFS for a non-timepoint stop -- must
    null scheduled_time (not pass the empty string through, which fails the
    same Postgres ::time cast as a non-numeric hour)."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agencies (agency_name, feed_url, ingest_strategy) "
            "VALUES (%s, %s, 'static_join') RETURNING agency_id",
            ("static_join_empty_sched_test", "http://empty-sched-test.example.com/feed.pb"),
        )
        aid = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO static_trips (agency_id, trip_id, route_id, service_id) VALUES (%s, %s, %s, %s)",
            (aid, "uuid-A", "R1", "平日"),
        )
        cur.execute(
            "INSERT INTO static_stop_times (agency_id, trip_id, stop_sequence, stop_id, departure_time) "
            "VALUES (%s, %s, %s, %s, %s)",
            (aid, "uuid-A", 1, "S1", ""),
        )
    pg_conn.commit()

    pb = _hex_pb_with_one_trip("uuid-A")
    rows = static_join.parse_feed(pb, "2026-05-09T12:00:00", "f1.bin", aid, pg_conn)

    assert len(rows) == 1
    assert rows[0][4] is None


def _static_join_agency_with_trips(conn, name: str, trips: list[tuple[str, str]]) -> int:
    """A static_join agency whose static schedule has each (trip_id, route_id)
    stopping once, at stop_sequence 1."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agencies (agency_name, feed_url, ingest_strategy) "
            "VALUES (%s, %s, 'static_join') RETURNING agency_id",
            (name, f"http://{name}.example.com/feed.pb"),
        )
        aid = cur.fetchone()[0]
        for trip_id, route_id in trips:
            cur.execute(
                "INSERT INTO static_trips (agency_id, trip_id, route_id, service_id) VALUES (%s, %s, %s, '平日')",
                (aid, trip_id, route_id),
            )
            cur.execute(
                "INSERT INTO static_stop_times (agency_id, trip_id, stop_sequence, stop_id, departure_time) "
                "VALUES (%s, %s, 1, 'S1', '07:05:00')",
                (aid, trip_id),
            )
    conn.commit()
    return aid


def test_static_join_takes_route_from_static_trips_when_the_feed_omits_it(pg_conn):
    """TripDescriptor.route_id is optional in GTFS-RT, and static_trips names
    the trip's route. A stop the schedule JOIN misses still belongs to the
    trip, so it gets the route too; a route_id the feed does send wins."""
    aid = _static_join_agency_with_trips(
        pg_conn, "static_join_route_fallback_test", [("uuid-A", "R1"), ("uuid-B", "R2"), ("uuid-C", "R3")]
    )
    pb = trip_update_feed(
        trip_update("uuid-A", stop_time_update(1, departure_delay=0), stop_time_update(9, departure_delay=0)),
        trip_update("uuid-B", stop_time_update(1, departure_delay=0), route_id="RT-B"),
        trip_update("uuid-C", stop_time_update(1, departure_delay=0), route_id=""),
        trip_update("uuid-unknown", stop_time_update(1, departure_delay=0)),
    )
    rows = static_join.parse_feed(pb, "2026-05-09T12:00:00", "f1.bin", aid, pg_conn)

    assert [(r[2], r[6], r[5]) for r in rows] == [
        ("uuid-A", 1, "R1"),
        ("uuid-A", 9, "R1"),
        ("uuid-B", 1, "RT-B"),
        ("uuid-C", 1, "R3"),
        ("uuid-unknown", 1, None),
    ]


def test_static_join_drops_only_the_stop_time_updates_without_a_stop_sequence(pg_conn):
    """A StopTimeUpdate may name only its stop_id, but stop_sequence is a
    non-Nullable UInt16 column: such an update, or one past UInt16, costs its
    own row instead of failing the insert for the whole poll."""
    aid = _static_join_agency_with_trips(pg_conn, "static_join_no_stop_sequence_test", [("uuid-A", "R1")])
    pb = trip_update_feed(
        trip_update(
            "uuid-A",
            stop_time_update(stop_id="S0", departure_delay=60),
            stop_time_update(1, departure_delay=-30),
            stop_time_update(70000, departure_delay=60),
            route_id="R1",
        )
    )
    rows = static_join.parse_feed(pb, "2026-05-09T12:00:00", "f1.bin", aid, pg_conn)

    assert [(r[6], r[7], r[4]) for r in rows] == [(1, -30, "07:05:00")]


def _cancelled_trip_agency(pg_conn, name: str, stop_times: dict[str, list[tuple[int, str, str]]], version=None):
    """An agency whose timetable runs each trip of *stop_times* on route R<n>,
    the n-th, calling at its (stop_sequence, stop_id, departure_time)s."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agencies (agency_name, feed_url, ingest_strategy) "
            "VALUES (%s, %s, 'static_join') RETURNING agency_id",
            (name, f"http://{name}.example.com/feed.pb"),
        )
        aid = cur.fetchone()[0]
        for n, (trip_id, stops) in enumerate(stop_times.items(), start=1):
            cur.execute(
                "INSERT INTO static_trips (agency_id, trip_id, route_id, service_id, static_version_id) "
                "VALUES (%s, %s, %s, '平日', %s)",
                (aid, trip_id, f"R{n}", version),
            )
            for seq, stop_id, departure in stops:
                cur.execute(
                    "INSERT INTO static_stop_times (agency_id, trip_id, stop_sequence, stop_id, departure_time) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (aid, trip_id, seq, stop_id, departure),
                )
    pg_conn.commit()
    return aid


def test_static_join_stores_a_cancelled_trip_listing_no_stop_as_a_withdrawal_per_scheduled_stop(pg_conn):
    """GTFS-RT lets a CANCELED TripUpdate list no StopTimeUpdate, and one
    whose updates all lack a usable stop_sequence stores none either. Such a
    trip is stored as one row per stop its timetable schedules it at and it
    has not left yet: the stop's sequence, stop and schedule, the trip's
    route, service and version, the CANCELED status and no delay. A stop_sequence no stop row can hold is
    left out. A trip the timetable lists no stop for keeps one trip-level row,
    with no stop, delay or schedule: it has no departure to carry. A cancellation that stores a stop row
    needs neither, a SCHEDULED trip listing no stop says nothing, and the
    probe still counts StopTimeUpdates alone. A poll holding nothing else
    stores the rows too."""
    from pipeline.clickhouse import TRIP_LEVEL_STOP_SEQUENCE

    version = "gtfs_static_20260101"
    aid = _cancelled_trip_agency(
        pg_conn,
        "static_join_cancelled_trip_test",
        {
            "uuid-A": [(1, "S1", "12:05:00"), (2, "S2", "25:10:00"), (70000, "S3", "25:20:00")],
            "uuid-B": [(1, "S1", "12:05:00")],
            "uuid-C": [(1, "S1", "12:05:00")],
            "uuid-D": [(1, "S1", "12:05:00")],
            "uuid-E": [],
        },
        version,
    )
    canceled, no_data = 3, 2
    pb = trip_update_feed(
        trip_update("uuid-A", schedule_relationship=canceled),
        trip_update(
            "uuid-B",
            stop_time_update(stop_id="S1", schedule_relationship=no_data),
            route_id="RT-B",
            schedule_relationship=canceled,
        ),
        trip_update("uuid-C", stop_time_update(1, schedule_relationship=no_data), schedule_relationship=canceled),
        trip_update("uuid-D", schedule_relationship=0),
        trip_update("uuid-E", schedule_relationship=canceled),
        trip_update("uuid-unknown", schedule_relationship=canceled),
        timestamp=1778295600,
    )
    at = "2026-05-09T12:00:00+09:00"
    rows = static_join.parse_feed(pb, at, "f1.bin", aid, pg_conn)

    def row(trip_id, service, scheduled_time, route, stop_sequence, stop_id, stop_status, scheduled_sec, version):
        # file_name, captured_at, trip_id, service_type, scheduled_time, route_code, stop_sequence,
        # dep_delay, stop_id, arr_delay, both relationships, feed_timestamp, scheduled_sec, version.
        return (
            *("f1.bin", at, trip_id, service, scheduled_time, route, stop_sequence, None, stop_id, None),
            *(canceled, stop_status, 1778295600, scheduled_sec, version),
        )

    trip_level = TRIP_LEVEL_STOP_SEQUENCE
    withdrawals_of_a = [
        row("uuid-A", "平日", "12:05:00", "R1", 1, "S1", None, 43500, version),
        row("uuid-A", "平日", None, "R1", 2, "S2", None, 90600, version),
    ]
    assert sorted(rows, key=lambda r: (r[2], r[6])) == [
        *withdrawals_of_a,
        row("uuid-B", "平日", "12:05:00", "RT-B", 1, "S1", None, 43500, version),
        row("uuid-C", "平日", "12:05:00", "R3", 1, None, no_data, 43500, version),
        row("uuid-E", "平日", None, "R5", trip_level, None, None, None, version),
        row("uuid-unknown", None, None, None, trip_level, None, None, None, None),
    ]
    assert static_join.field_coverage(pb)["stop_time_updates"] == 2
    alone = trip_update_feed(trip_update("uuid-A", schedule_relationship=canceled), timestamp=1778295600)
    assert static_join.parse_feed(alone, at, "f1.bin", aid, pg_conn) == withdrawals_of_a


@pytest.mark.parametrize(
    ("captured_at", "withdrawn"),
    [
        ("2026-05-09T06:50:00+09:00", [1, 2, 3, 4, 5, 6]),
        ("2026-05-09T07:00:00+09:00", [1, 2, 3, 4, 5, 6]),
        ("2026-05-09T07:05:00+09:00", [2, 3, 4, 5, 6]),
        ("2026-05-09T07:12:00+09:00", [4, 5, 6]),
        ("2026-05-10T00:05:00+09:00", [6]),
        ("2026-05-10T03:50:00+09:00", [1, 3]),
    ],
    ids=["before_departure", "at_departure", "mid_run", "past_a_stop_with_no_time", "after_midnight", "next_days_run"],
)
def test_a_stop_less_cancellation_withdraws_only_the_stops_still_to_come(pg_conn, captured_at, withdrawn):
    """A CANCELED trip will not run the rest of its trip; a stop it already
    left keeps its reports. A stop is left once its scheduled departure on the
    trip's service day is earlier than the capture, and a stop with no time
    once a stop after it is. The trip's service day is the one its first
    departure is filed under (pipeline.service_day.filed_date_of): at 03:50,
    the 07:00 trip about to run is the next service day's, so none of its
    stops is left. Only the stops whose own reading files under that day are
    withdrawn by this poll: the later ones, and those with no time, file under
    the day before, and a poll after the cutover withdraws them."""
    aid = _cancelled_trip_agency(
        pg_conn,
        "static_join_cancel_to_come",
        {
            "T1": [
                (1, "S1", "07:00:00"),
                (2, "S2", ""),
                (3, "S3", "07:10:00"),
                (4, "S4", ""),
                (5, "S5", "23:50:00"),
                (6, "S6", "24:10:00"),
            ]
        },
        "gtfs_static_20260101",
    )
    cancelled = trip_update_feed(trip_update("T1", schedule_relationship=3), timestamp=1778295600)
    rows = static_join.parse_feed(cancelled, captured_at, "f1.bin", aid, pg_conn)
    assert [r[6] for r in rows] == withdrawn


@pytest.mark.parametrize(
    ("captured_at", "trips_day", "withdrawn"),
    [
        ("2026-05-10T03:50:00+09:00", "2026-05-10", [1]),
        ("2026-05-09T05:00:00+09:00", "2026-05-09", [1]),
    ],
    ids=["next_days_run", "a_late_stop_a_reading_would_file_the_day_before"],
)
def test_every_withdrawal_of_a_stop_less_cancellation_files_under_its_trips_day(
    pg_conn, captured_at, trips_day, withdrawn
):
    """A withdrawal stored under another service day would share that day's
    stop event with a run of the same trip there and could replace its real
    last reading, so a stop whose reading would file elsewhere is left to a
    later poll. At 05:00 a 27:50 stop's reading files under the day before,
    whose own 27:50 departure was an hour earlier."""
    from datetime import date, datetime

    from pipeline.service_day import filed_date_of

    aid = _cancelled_trip_agency(
        pg_conn,
        "static_join_cancel_trips_day",
        {"T1": [(1, "S1", "07:00:00"), (2, "S2", "27:50:00")]},
        "gtfs_static_20260101",
    )
    cancelled = trip_update_feed(trip_update("T1", schedule_relationship=3), timestamp=1778295600)
    rows = static_join.parse_feed(cancelled, captured_at, "f1.bin", aid, pg_conn)
    assert [r[6] for r in rows] == withdrawn
    captured = datetime.fromisoformat(captured_at)
    assert {filed_date_of(captured, r[13]) for r in rows} == {date.fromisoformat(trips_day)}


def test_a_stop_less_cancellation_after_the_last_stop_keeps_only_the_trip_level_row(pg_conn):
    """Every stop already left keeps its reports, so the cancellation is kept
    as the trip-level row alone. It carries the trip's last scheduled
    departure, so it is filed under, and held to, the trip's own day."""
    from pipeline.clickhouse import TRIP_LEVEL_STOP_SEQUENCE

    aid = _cancelled_trip_agency(
        pg_conn, "static_join_cancel_after_run", {"T1": [(1, "S1", "07:00:00"), (2, "S2", "07:10:00")]}, "v1"
    )
    cancelled = trip_update_feed(trip_update("T1", schedule_relationship=3), timestamp=1778295600)
    rows = static_join.parse_feed(cancelled, "2026-05-09T07:11:00+09:00", "f1.bin", aid, pg_conn)
    assert [(r[6], r[3], r[5], r[10], r[13], r[14]) for r in rows] == [
        (TRIP_LEVEL_STOP_SEQUENCE, "平日", "R1", 3, 7 * 3600 + 10 * 60, "v1")
    ]


def test_the_trip_level_row_of_a_run_that_ran_before_the_cutover_files_under_its_own_day(pg_conn):
    """At 04:30 a night trip whose stops were scheduled at 25:30 and 26:30 has
    left them all on the day before, which is the trip's day; its trip-level
    row must file there, not under the capture's own day, where another run
    of the trip may be about to start."""
    from datetime import date, datetime

    from pipeline.clickhouse import TRIP_LEVEL_STOP_SEQUENCE
    from pipeline.service_day import filed_date_of

    aid = _cancelled_trip_agency(
        pg_conn, "static_join_cancel_night_run", {"T1": [(1, "S1", "25:30:00"), (2, "S2", "26:30:00")]}, "v1"
    )
    cancelled = trip_update_feed(trip_update("T1", schedule_relationship=3), timestamp=1778295600)
    captured_at = "2026-05-10T04:30:00+09:00"
    rows = static_join.parse_feed(cancelled, captured_at, "f1.bin", aid, pg_conn)
    assert [r[6] for r in rows] == [TRIP_LEVEL_STOP_SEQUENCE]
    assert filed_date_of(datetime.fromisoformat(captured_at), rows[0][13]) == date(2026, 5, 9)


def test_a_stop_less_cancellation_stores_each_stop_under_its_earlier_predictions_keys(pg_conn):
    """Each stop's latest report decides whether it departed only among the
    reports of that stop event, which every dedup keys on the trip, stop,
    route, service, schedule and version a row stores. The withdrawal of each
    stop therefore carries the keys its prediction carried, including the
    route when the predictions name the static route and the cancellation
    names none."""
    aid = _cancelled_trip_agency(
        pg_conn,
        "static_join_cancel_keys",
        {"T1": [(1, "S1", "07:05:00"), (2, "S2", "24:10:00")]},
        "gtfs_static_20260101",
    )
    predicted = trip_update_feed(
        trip_update(
            "T1", stop_time_update(1, departure_delay=60), stop_time_update(2, departure_delay=60), route_id="R1"
        ),
        timestamp=1778295600,
    )
    cancelled = trip_update_feed(trip_update("T1", schedule_relationship=3), timestamp=1778295660)

    def keys(rows):
        # trip_id, service_type, scheduled_time, route_code, stop_sequence, scheduled_sec, static_version_id
        return [(r[2], r[3], r[4], r[5], r[6], r[13], r[14]) for r in sorted(rows, key=lambda r: r[6])]

    before = static_join.parse_feed(predicted, "2026-05-09T07:00:00+09:00", "f1.bin", aid, pg_conn)
    after = static_join.parse_feed(cancelled, "2026-05-09T07:01:00+09:00", "f2.bin", aid, pg_conn)
    assert keys(after) == keys(before)
    assert [(r[7], r[10]) for r in after] == [(None, 3), (None, 3)]


def test_a_cancelled_trips_stops_are_read_afresh_at_each_poll(pg_conn):
    """A cancelled trip stays in the feed poll after poll, and each poll reads
    its stops as the timetable stands then, under the same version name too:
    nothing is held between polls."""
    aid = _cancelled_trip_agency(
        pg_conn, "static_join_cancel_fresh", {"T1": [(1, "S1", "07:00:00"), (2, "S2", "07:08:00")]}, "v1"
    )
    cancelled = trip_update_feed(trip_update("T1", schedule_relationship=3), timestamp=1778295600)

    def schedule(poll: str) -> list:
        rows = static_join.parse_feed(cancelled, "2026-05-11T06:50:00+09:00", poll, aid, pg_conn)
        return [(r[6], r[4]) for r in rows]

    assert schedule("f1.bin") == [(1, "07:00:00"), (2, "07:08:00")]
    with pg_conn.cursor() as cur:
        cur.execute("UPDATE static_stop_times SET departure_time = '07:01:00' WHERE agency_id = %s", (aid,))
    pg_conn.commit()
    assert schedule("f2.bin") == [(1, "07:01:00"), (2, "07:01:00")]


# Integration tests against captured fixtures


def _make_agency(conn, name: str, feed_url: str) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agencies (agency_name, feed_url, ingest_strategy) "
            "VALUES (%s, %s, 'static_join') RETURNING agency_id",
            (name, feed_url),
        )
        aid = cur.fetchone()[0]
    conn.commit()
    return aid


def _run_and_assert(conn, aid: int, pb_path: pathlib.Path, static_version_id: str | None = None):
    raw = pb_path.read_bytes()
    rows = static_join.parse_feed(raw, "2026-05-09T12:00:00", "test/sample.bin", aid, conn)
    assert rows, "static_join returned zero rows; pb may be empty or malformed"

    with_route = [r for r in rows if r[5] is not None]
    with_svc = [r for r in rows if r[3] is not None]
    with_sched = [r for r in rows if r[4] is not None]

    # route_code is from RT.route_id and must always be present
    assert len(with_route) == len(rows), f"route_code missing on {len(rows) - len(with_route)} rows"
    # JOIN coverage budget: >=99% of rows have service_type and scheduled_time
    cov_svc = len(with_svc) / len(rows)
    cov_sched = len(with_sched) / len(rows)
    assert cov_svc >= 0.99, f"service_type JOIN coverage {cov_svc:.2%}"
    assert cov_sched >= 0.99, f"scheduled_time JOIN coverage {cov_sched:.2%}"

    # Hiroshima-style feeds (this parametrization's 3 agencies) populate
    # stop_id and both schedule_relationship fields on essentially every
    # stop_time_update, and FeedHeader.timestamp once per feed message --
    # round-trip coverage should match, near-universally.
    cov_stop_id = sum(1 for r in rows if r[8] is not None) / len(rows)
    cov_sched_rel_trip = sum(1 for r in rows if r[10] is not None) / len(rows)
    cov_sched_rel_stop = sum(1 for r in rows if r[11] is not None) / len(rows)
    cov_feed_ts = sum(1 for r in rows if r[12] is not None) / len(rows)
    assert cov_stop_id >= 0.99, f"stop_id coverage {cov_stop_id:.2%}"
    assert cov_sched_rel_trip >= 0.99, f"schedule_relationship_trip coverage {cov_sched_rel_trip:.2%}"
    assert cov_sched_rel_stop >= 0.99, f"schedule_relationship_stop coverage {cov_sched_rel_stop:.2%}"
    assert cov_feed_ts == 1.0, f"feed_timestamp coverage {cov_feed_ts:.2%}"
    # Non-NULL alone doesn't prove the right protobuf field was read -- a
    # neighboring field (e.g. a small enum like FeedHeader.incrementality)
    # would also satisfy a bare not-None check. A real GTFS-RT feed's
    # FeedHeader.timestamp is a Unix epoch in seconds, so it must be a large
    # value; assert magnitude, not just presence.
    feed_ts_values = {r[12] for r in rows if r[12] is not None}
    assert all(v > 1_600_000_000 for v in feed_ts_values), f"feed_timestamp implausible: {feed_ts_values}"

    # arr_delay is only sent when the StopTimeUpdate carries an `arrival`
    # submessage (sparse by design, not a bug) -- assert it's genuinely
    # present some of the time without demanding near-universal coverage.
    cov_arr_delay = sum(1 for r in rows if r[9] is not None) / len(rows)
    assert 0.0 < cov_arr_delay < 0.5, f"arr_delay coverage {cov_arr_delay:.2%} (expected sparse, nonzero)"

    # scheduled_sec is set whenever the static JOIN hits and departure_time
    # parses (status "ok" or "extended" -- see parse_departure_time), so its
    # coverage tracks scheduled_time's JOIN coverage budget above.
    cov_scheduled_sec = sum(1 for r in rows if r[13] is not None) / len(rows)
    assert cov_scheduled_sec >= 0.99, f"scheduled_sec coverage {cov_scheduled_sec:.2%}"

    # static_version_id comes from the same static_trips row the JOIN
    # matched, so it's present for every row with a resolved service_type,
    # and (since load_static sets one fixed value per call) must be the
    # loaded zip's own filename stem -- not just any non-NULL value.
    cov_static_version_id = sum(1 for r in rows if r[14] is not None) / len(rows)
    assert cov_static_version_id >= 0.99, f"static_version_id coverage {cov_static_version_id:.2%}"
    if static_version_id is not None:
        version_values = {r[14] for r in rows if r[14] is not None}
        assert version_values == {static_version_id}, f"static_version_id mismatch: {version_values}"


@pytest.mark.parametrize(
    "feed_url, pb_name, zip_name, agency_label",
    [
        (
            "https://ajt-mobusta-gtfs.mcapps.jp/realtime/8/trip_updates.bin",
            "hiroden_tu.bin",
            "hiroden_static.zip",
            "広島電鉄_test",
        ),
        (
            "https://ajt-mobusta-gtfs.mcapps.jp/realtime/9/trip_updates.bin",
            "hirobus_tu.bin",
            "hirobus_static.zip",
            "広島バス_test",
        ),
        (
            "https://ajt-mobusta-gtfs.mcapps.jp/realtime/10/trip_updates.bin",
            "hirokoh_tu.bin",
            "hirokoh_static.zip",
            "広島交通_test",
        ),
    ],
)
def test_static_join_per_op(pg_conn, feed_url, pb_name, zip_name, agency_label, ch_client):
    aid = _make_agency(pg_conn, agency_label, feed_url)
    load_static(str(FIX / zip_name), aid, pg_conn, ch_client=ch_client)
    _run_and_assert(pg_conn, aid, FIX / pb_name, static_version_id=pathlib.Path(zip_name).stem)


def test_load_static_geiyo_fixture_row_counts(pg_conn, ch_client):
    """geiyo_static.zip is a real vendored GTFS static feed for agency 11
    (Geiyo Bus). Asserts fixed row counts (1682 stops, 64 routes, 1593 trips,
    44152 stop_times) for this checked-in, immutable fixture zip so a future
    `load_static` regression or accidental re-vendoring is caught.

    geiyo_tu.bin (the paired RT capture) is a known-empty overnight snapshot
    (0 stop_time_updates) that proves nothing about per-field RT coverage --
    an agency only earns a verdict in `rt_field_coverage_probes` once a
    live, in-service probe confirms real coverage -- so unlike
    `test_static_join_per_op` this only exercises the static load, not
    parse_feed/field_coverage.
    """
    aid = _make_agency(pg_conn, "芸陽バス_test", "https://ajt-mobusta-gtfs.mcapps.jp/realtime/11/trip_updates.bin")
    load_static(str(FIX / "geiyo_static.zip"), aid, pg_conn, ch_client=ch_client)

    with pg_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM static_stops WHERE agency_id = %s", (aid,))
        assert cur.fetchone()[0] == 1682
        cur.execute("SELECT count(*) FROM static_routes WHERE agency_id = %s", (aid,))
        assert cur.fetchone()[0] == 64
        cur.execute("SELECT count(*) FROM static_trips WHERE agency_id = %s", (aid,))
        assert cur.fetchone()[0] == 1593
        cur.execute("SELECT count(*) FROM static_stop_times WHERE agency_id = %s", (aid,))
        assert cur.fetchone()[0] == 44152


@pytest.mark.parametrize("pb_name", ["hiroden_tu.bin", "hirobus_tu.bin", "hirokoh_tu.bin"])
def test_field_coverage_matches_known_fixture_stats(pb_name):
    """field_coverage() -- the per-field coverage check used by
    scripts/probe_rt_field_coverage.py to vet a live feed -- needs no DB
    connection or static schedule, and must reproduce the same per-field
    coverage `_run_and_assert` already confirms for these captured fixtures
    via the full parse_feed + JOIN path -- proving the standalone probe
    agrees with the production decode path rather than silently drifting
    from it.
    """
    raw = (FIX / pb_name).read_bytes()
    cov = static_join.field_coverage(raw)

    assert cov["stop_time_updates"] > 0
    assert cov["feed_timestamp"] is not None
    assert cov["feed_timestamp"] > 1_600_000_000

    assert cov["stop_id_coverage"] >= 0.99
    assert cov["schedule_relationship_trip_coverage"] >= 0.99
    assert cov["schedule_relationship_stop_coverage"] >= 0.99
    # Sparse-by-design (only sent when a StopTimeUpdate carries an `arrival`
    # submessage), not a bug -- same bound _run_and_assert checks.
    assert 0.0 < cov["arr_delay_coverage"] < 0.5


def test_field_coverage_empty_feed_reports_zero_without_coverage_keys():
    """An empty/undecodable feed says nothing about a field's population
    habits -- coverage keys must be omitted, not misreported as 0.0 (which
    would look identical to "confirmed always absent")."""
    cov = static_join.field_coverage(b"")
    assert cov == {"stop_time_updates": 0, "feed_timestamp": None}
