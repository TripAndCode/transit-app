"""Hiroshima-style RT ingest strategy.

The trip_id in these feeds is an opaque UUID; route_code, service_type, and
scheduled_time are derived by JOINing to static_trips and static_stop_times
on (agency_id, trip_id, stop_sequence).

Rows where the JOIN misses get NULLs in service_type / scheduled_time;
route_code is taken straight from the RT trip.route_id and is always non-null.
"""

import logging

from pipeline.strategies._pb import _dec, _fields
from pipeline.strategies._time import normalize_departure_time

_log = logging.getLogger(__name__)


def _decode_rows(pb_bytes: bytes):
    """Yield (trip_id, rt_route_id, stop_sequence, dep_delay) per stop_time_update."""
    try:
        top = _fields(pb_bytes)
    except Exception:
        return
    for ent_bytes in top.get(2, []):
        ent = _fields(ent_bytes)
        if 3 not in ent:
            continue
        tu = _fields(ent[3][0])
        trip_id = rt_route_id = None
        if 1 in tu:
            trip = _fields(tu[1][0])
            if 1 in trip:
                trip_id = _dec(trip[1][0])
            if 5 in trip:
                rt_route_id = _dec(trip[5][0])
        if not trip_id:
            continue
        for stu_bytes in tu.get(2, []):
            stu = _fields(stu_bytes)
            stop_seq = stu.get(1, [None])[0]
            dep_delay = None
            if 3 in stu:
                dep = _fields(stu[3][0])
                dep_delay = dep.get(1, [None])[0]
            yield (trip_id, rt_route_id, stop_seq, dep_delay)


def parse_feed(
    pb_bytes: bytes,
    captured_at: str,
    file_name: str,
    agency_id: int,
    conn,
) -> list:
    """Return rows shaped for pipeline.clickhouse.insert_updates.

    Row shape: (file_name, captured_at, trip_id, service_type, scheduled_time,
                route_code, stop_sequence, dep_delay).
    """
    raw_rows = list(_decode_rows(pb_bytes))
    if not raw_rows:
        return []

    keys = list({(r[0], r[2]) for r in raw_rows if r[2] is not None})
    if not keys:
        return []

    trip_ids = [k[0] for k in keys]
    stop_seqs = [k[1] for k in keys]

    with conn.cursor() as cur:
        # No-op after the first call on this connection: temp tables persist
        # for the whole session (default ON COMMIT PRESERVE ROWS), not just
        # one transaction, and `CREATE TABLE IF NOT EXISTS ... AS SELECT`
        # only runs the SELECT the first time the table doesn't yet exist.
        # ingest() opens one connection per CLI `ingest` invocation (one
        # agency per process, see cmd_ingest in gtfs_pipeline.py), so "once
        # per connection" means "once per agency backfill run" -- this
        # intentionally does NOT pick up a static schedule change made
        # mid-run (accepted trade-off; static GTFS data doesn't change
        # during a historical backfill in practice).
        cur.execute(
            "CREATE TEMP TABLE IF NOT EXISTS _sj_schedule AS "
            "SELECT t.trip_id, st.stop_sequence, t.service_id, st.departure_time "
            "FROM static_stop_times st "
            "JOIN static_trips t ON t.agency_id = st.agency_id AND t.trip_id = st.trip_id "
            "WHERE st.agency_id = %s",
            (agency_id,),
        )
        cur.execute("CREATE INDEX IF NOT EXISTS _sj_schedule_idx ON _sj_schedule (trip_id, stop_sequence)")
        cur.execute(
            "SELECT s.trip_id, s.stop_sequence, s.service_id, s.departure_time "
            "FROM _sj_schedule s "
            "JOIN unnest(%s::text[], %s::int[]) AS k(trip_id, stop_sequence) "
            "  ON k.trip_id = s.trip_id AND k.stop_sequence = s.stop_sequence",
            (trip_ids, stop_seqs),
        )
        joined = {(tid, seq): (svc, dep) for (tid, seq, svc, dep) in cur.fetchall()}

    rows = []
    miss = 0
    skipped_extended = 0
    bad_sched = 0
    for trip_id, rt_route_id, stop_seq, dep_delay in raw_rows:
        svc, sched = joined.get((trip_id, stop_seq), (None, None))
        if svc is None and sched is None:
            miss += 1
        # Single parse drives both the extended-hour drop and the zero-pad
        # (see pipeline/strategies/_time.py's module docstring for why: two
        # independent parses of the same field -- this used to check
        # sched[:2] for the >=24 decision and hour_str.isdigit() for padding
        # separately -- can disagree on malformed multi-digit input, e.g. a
        # 3-digit hour like "125:30:00" had a fine 2-char prefix but a
        # broken zero-pad, storing an uncastable value). GTFS's
        # departure_time is raw, unpadded, unvalidated text -- "7:05:00" is
        # as valid as "07:05:00" per spec, and nothing guarantees the rest
        # is even numeric. Postgres's old TIME column validated and
        # normalized this for free; ClickHouse's plain String does not, so
        # analyze()'s `_analyze_deduped.scheduled_time time` column is now
        # the only place format errors surface -- and once a bad value is
        # durably stored in ClickHouse, it fails analyze() for this ENTIRE
        # agency on every subsequent run, not just drops one row. NULL is
        # already a first-class value on this column (every downstream read
        # site handles it -- WHERE scheduled_time IS NOT NULL / toUInt8OrNull).
        sched, status = normalize_departure_time(sched)
        if status == "extended":
            # GTFS allows departure_time like "25:30:00" for trips spanning
            # midnight as continuation of the previous service day, with no
            # representation this column (Nullable(String), but read
            # everywhere as a same-day HH:MM[:SS]) can hold; drop the row + log.
            _log.warning("static_join: skipping trip_id=%r seq=%s extended departure_time", trip_id, stop_seq)
            skipped_extended += 1
            continue
        if status == "bad":
            bad_sched += 1
        rows.append(
            (
                file_name,
                captured_at,
                trip_id,
                svc,
                sched,
                rt_route_id,
                stop_seq,
                dep_delay,
            )
        )

    if miss:
        _log.info(f"[static_join] agency={agency_id} {miss}/{len(rows) + skipped_extended} rows missed JOIN (logged)")
    if skipped_extended:
        _log.warning(f"[static_join] agency={agency_id} {skipped_extended} rows dropped (hour >= 24)")
    if bad_sched:
        _log.warning(f"[static_join] agency={agency_id} {bad_sched} rows had a non-numeric departure_time hour")
    return rows
