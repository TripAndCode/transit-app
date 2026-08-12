"""static_join strategy tests: repeated-call regression + per-op fixture integration."""

import pathlib

import pytest

from pipeline.static_loader import load_static
from pipeline.strategies import static_join

FIX = pathlib.Path(__file__).parent.parent / "fixtures"


def _hex_pb_with_one_trip(trip_id: str, route_id: str = "R1") -> bytes:
    """Hand-craft a minimal GTFS-RT FeedMessage with one TripUpdate
    referencing trip_id + route_id and one stop_time_update with stop_sequence=1.

    This avoids needing a real fixture for this regression test.
    """

    # Use the existing varint helpers — but we need to encode, so do it inline.
    def varint(n):
        out = bytearray()
        while n > 0x7F:
            out.append((n & 0x7F) | 0x80)
            n >>= 7
        out.append(n & 0x7F)
        return bytes(out)

    def field_string(field_num, value):
        v = value.encode("utf-8")
        return varint((field_num << 3) | 2) + varint(len(v)) + v

    def field_uint(field_num, value):
        return varint((field_num << 3) | 0) + varint(value)

    def field_submsg(field_num, body):
        return varint((field_num << 3) | 2) + varint(len(body)) + body

    # TripDescriptor: field 1 = trip_id (str), field 5 = route_id (str)
    trip = field_string(1, trip_id) + field_string(5, route_id)

    # StopTimeUpdate: field 1 = stop_sequence (uint), field 3 = StopTimeEvent {1: delay}
    dep = field_uint(1, 0)  # delay = 0
    stu = field_uint(1, 1) + field_submsg(3, dep)

    # TripUpdate: field 1 = trip (TripDescriptor), field 2 = stop_time_update (repeated)
    tu = field_submsg(1, trip) + field_submsg(2, stu)

    # FeedEntity: field 3 = trip_update
    ent = field_submsg(3, tu)

    # FeedMessage: field 1 = header (skipped), field 2 = entity (repeated)
    msg = field_submsg(2, ent)
    return msg


def test_static_join_handles_repeated_calls_same_transaction(pg_conn):
    """The temp table _sj_keys must not collide across consecutive parse_feed
    calls inside one transaction. Regression for the ON COMMIT DROP / no-commit
    interaction with ingest.py's batch-commit cadence.
    """
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


def test_static_join_zero_pads_single_digit_hour_scheduled_time(pg_conn):
    """GTFS's departure_time is raw, unpadded text — "7:05:00" is as valid
    as "07:05:00" per spec. Postgres's old TIME column normalized this for
    free; ClickHouse's plain String does not, and every hour-extraction
    read site downstream assumes a 2-digit hour. Regression: static_join
    must zero-pad at write time so a single-digit-hour departure_time
    doesn't sort into the wrong time band or crash toUInt8() reads."""
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
    (which analyze()'s `_analyze_deduped.scheduled_time time` column can't
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


# ---------------------------------------------------------------------------
# Integration tests against captured fixtures
# ---------------------------------------------------------------------------


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


def _run_and_assert(conn, aid: int, pb_path: pathlib.Path):
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
def test_static_join_per_op(pg_conn, feed_url, pb_name, zip_name, agency_label):
    aid = _make_agency(pg_conn, agency_label, feed_url)
    load_static(str(FIX / zip_name), aid, pg_conn)
    _run_and_assert(pg_conn, aid, FIX / pb_name)
