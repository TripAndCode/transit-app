"""Bug-regression test for the temp-table collision."""

from pipeline.strategies import static_join


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
