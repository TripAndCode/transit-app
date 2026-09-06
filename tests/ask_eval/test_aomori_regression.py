"""Aomori regression-lock test.

Runs parse_pb on the captured fixture and asserts byte-identical output
against tests/fixtures/aomori_golden.json. This test must pass on every
commit from now on; if a refactor changes Aomori output, the test fails
and the refactor is rejected.
"""

import json
import pathlib

from pipeline.ingest import _ts, parse_pb

FIX_DIR = pathlib.Path(__file__).parent.parent / "fixtures"


def test_aomori_parse_pb_matches_golden():
    raw = (FIX_DIR / "aomori_sample.pb").read_bytes()
    captured_at = _ts("20260509", "TripUpdate_120000.pb")

    rows = parse_pb(raw, captured_at, "20260509/TripUpdate_120000.pb")
    actual = [list(r) for r in rows]

    expected = json.loads((FIX_DIR / "aomori_golden.json").read_text())

    assert actual == expected, (
        "Aomori parse_pb output diverged from golden. "
        "If this is intentional, regenerate via scripts/capture_aomori_golden.py."
    )


def test_aomori_strategy_matches_golden():
    """The aomori_regex strategy must produce the same effective rows the
    legacy parse_pb does (modulo dropped fields the strategy never emits).
    """
    # The strategy needs a DB connection only to look up trip_id_pattern;
    # use the same conftest-managed test DB.
    import os
    import time

    import psycopg2

    from pipeline.strategies import aomori_regex
    from pipeline.strategies._pb import decode_feed_timestamp

    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    try:
        with conn.cursor() as cur:
            # Use unique feed_url to avoid conflicts when tests run sequentially
            feed_url = f"http://aomori-test-{int(time.time())}.example.com/feed.pb"
            cur.execute(
                "INSERT INTO agencies (agency_name, feed_url) VALUES (%s, %s) RETURNING agency_id",
                ("青森市バス_test", feed_url),
            )
            aid = cur.fetchone()[0]
        conn.commit()

        raw = (FIX_DIR / "aomori_sample.pb").read_bytes()
        captured_at = _ts("20260509", "TripUpdate_120000.pb")
        rows = aomori_regex.parse_feed(raw, captured_at, "20260509/TripUpdate_120000.pb", aid, conn)
    finally:
        conn.rollback()
        conn.close()

    expected_full = json.loads((FIX_DIR / "aomori_golden.json").read_text())
    feed_timestamp = decode_feed_timestamp(raw)
    # Project legacy 12-tuple to the 13-tuple the strategy emits
    # (file_name, captured_at, trip_id, service, sched, route, stop_seq, dep_delay,
    #  stop_id, arr_delay, schedule_relationship_trip, schedule_relationship_stop,
    #  feed_timestamp) -- this feed's TripUpdate/StopTimeUpdate messages never set
    # stop_id, arrival, or either schedule_relationship field, so the legacy
    # golden's r[7]/r[8] (already null) cover stop_id/arr_delay and the two
    # schedule_relationship fields are always None here; feed_timestamp comes from
    # the same feed message's header, decoded independently of the golden fixture.
    expected = [
        [r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[10], r[7], r[8], None, None, feed_timestamp] for r in expected_full
    ]
    actual = [list(r) for r in rows]
    assert actual == expected

    # The comparison above reuses decode_feed_timestamp() to build `expected`,
    # so it can't by itself catch a bug inside that function (both sides would
    # be wrong together). Independently sanity-check the value actually
    # produced by parse_feed against a hardcoded plausibility bound: a real
    # GTFS-RT FeedHeader.timestamp is a Unix epoch in seconds, not a small
    # enum-sized value, so every row must carry the same large number.
    feed_timestamps = {r[12] for r in actual}
    assert len(feed_timestamps) == 1, f"feed_timestamp should be constant per feed message, got {feed_timestamps}"
    (only_feed_timestamp,) = feed_timestamps
    assert only_feed_timestamp > 1_600_000_000, f"feed_timestamp implausible: {only_feed_timestamp}"
