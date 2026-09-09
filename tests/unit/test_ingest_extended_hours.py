"""aomori_regex still rejects an extended-hours (>=24) trip_id outright (its
trip_id-embedded hour/minute has no static-table JOIN to fall back to, and a
strict TIME column -- migration 0011 -- historically couldn't hold it).
static_join.py instead keeps the row and stores the raw
seconds-since-service-day-start value in scheduled_sec (see
pipeline/strategies/_time.py's parse_departure_time)."""

from pipeline.strategies._time import parse_departure_time
from pipeline.strategies.aomori_regex import _TRIP_RE_DEFAULT, parse_trip_id


def test_parse_trip_id_extracts_extended_hour():
    """parse_trip_id matches any digits; the rejection happens in
    parse_feed, not at regex match time. This pins that contract."""
    parsed = parse_trip_id("平日_25時30分_系統10", pattern=_TRIP_RE_DEFAULT)
    assert parsed is not None
    assert parsed["hour"] == "25"


def test_static_join_loop_keeps_extended_hour_row_with_scheduled_sec():
    """Mirror of the inner loop in static_join.parse_feed: a row whose
    joined departure_time has hour >= 24 is kept (not dropped) with
    scheduled_time NULL and scheduled_sec set to the raw seconds value;
    a normal row gets both scheduled_time and scheduled_sec. This is a
    logic-level pin, not a wire-level fixture — the full protobuf path is
    exercised by the ingest smoke suite.

    Calls the actual parse_departure_time() rather than re-implementing its
    logic inline: an earlier version of this test hand-rolled the
    sched[:2].isdigit() check production used at the time, and silently
    stopped reflecting production once static_join.py moved to the shared
    helper -- this test kept passing against its own stale copy while
    production's validation logic diverged underneath it."""
    raw_rows = [
        ("trip_a", "route10", 1, 60),
        ("trip_b", "route10", 1, 60),
    ]
    joined = {
        ("trip_a", 1): ("平日", "10:30:00"),
        ("trip_b", 1): ("平日", "25:30:00"),
    }
    kept = []
    extended = 0
    for trip_id, _rt_route_id, stop_seq, dep_delay in raw_rows:
        svc, sched = joined.get((trip_id, stop_seq), (None, None))
        sched, status, scheduled_sec = parse_departure_time(sched)
        if status == "extended":
            extended += 1
        kept.append((trip_id, svc, sched, dep_delay, scheduled_sec))
    assert [r[0] for r in kept] == ["trip_a", "trip_b"]
    assert extended == 1
    trip_a, trip_b = kept
    assert trip_a[2] == "10:30:00" and trip_a[4] == 10 * 3600 + 30 * 60
    assert trip_b[2] is None and trip_b[4] == 25 * 3600 + 30 * 60
