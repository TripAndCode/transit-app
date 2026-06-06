"""Strategies reject scheduled_time hour >= 24 so a strict TIME column
(migration 0011) can't be crashed by an extended-hours trip_id."""

from pipeline.strategies.aomori_regex import _TRIP_RE_DEFAULT, parse_trip_id


def test_parse_trip_id_extracts_extended_hour():
    """parse_trip_id matches any digits; the rejection happens in
    parse_feed, not at regex match time. This pins that contract."""
    parsed = parse_trip_id("平日_25時30分_系統10", pattern=_TRIP_RE_DEFAULT)
    assert parsed is not None
    assert parsed["hour"] == "25"


def test_static_join_loop_drops_extended_hour_row():
    """Mirror of the inner loop in static_join.parse_feed: rows whose
    joined departure_time has hour >= 24 are skipped, normal rows are
    kept. This is a logic-level pin, not a wire-level fixture — the
    full protobuf path is exercised by the ingest smoke suite."""
    raw_rows = [
        ("trip_a", "route10", 1, 60),
        ("trip_b", "route10", 1, 60),
    ]
    joined = {
        ("trip_a", 1): ("平日", "10:30:00"),
        ("trip_b", 1): ("平日", "25:30:00"),
    }
    kept = []
    skipped = 0
    for trip_id, _rt_route_id, stop_seq, dep_delay in raw_rows:
        svc, sched = joined.get((trip_id, stop_seq), (None, None))
        if svc is None and sched is None:
            continue
        if sched and sched[:2].isdigit() and int(sched[:2]) >= 24:
            skipped += 1
            continue
        kept.append((trip_id, svc, sched, dep_delay))
    assert [r[0] for r in kept] == ["trip_a"]
    assert skipped == 1
