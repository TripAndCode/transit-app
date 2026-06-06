"""Hiroshima-style RT ingest strategy.

The trip_id in these feeds is an opaque UUID; route_code, service_type, and
scheduled_time are derived by JOINing to static_trips and static_stop_times
on (agency_id, trip_id, stop_sequence).

Rows where the JOIN misses get NULLs in service_type / scheduled_time;
route_code is taken straight from the RT trip.route_id and is always non-null.
"""

import logging

from pipeline.strategies._pb import _dec, _fields

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
    """Return rows shaped for UPDATE_INSERT_SQL.

    Row shape: (file_name, captured_at, trip_id, service_type, scheduled_time,
                route_code, stop_sequence, dep_delay).
    """
    raw_rows = list(_decode_rows(pb_bytes))
    if not raw_rows:
        return []

    keys = list({(r[0], r[2]) for r in raw_rows if r[2] is not None})
    if not keys:
        return []

    from psycopg2.extras import execute_values

    with conn.cursor() as cur:
        cur.execute("CREATE TEMP TABLE IF NOT EXISTS _sj_keys (trip_id TEXT, stop_sequence INT) ON COMMIT DROP")
        cur.execute("TRUNCATE _sj_keys")
        execute_values(cur, "INSERT INTO _sj_keys VALUES %s", keys)
        cur.execute(
            """
            SELECT t.trip_id, st.stop_sequence, t.service_id, st.departure_time
            FROM _sj_keys k
            JOIN static_stop_times st
              ON st.agency_id = %s
             AND st.trip_id = k.trip_id
             AND st.stop_sequence = k.stop_sequence
            JOIN static_trips t
              ON t.agency_id = st.agency_id
             AND t.trip_id = st.trip_id
            """,
            (agency_id,),
        )
        joined = {(tid, seq): (svc, dep) for (tid, seq, svc, dep) in cur.fetchall()}

    rows = []
    miss = 0
    skipped_extended = 0
    for trip_id, rt_route_id, stop_seq, dep_delay in raw_rows:
        svc, sched = joined.get((trip_id, stop_seq), (None, None))
        if svc is None and sched is None:
            miss += 1
        elif sched and sched[:2].isdigit() and int(sched[:2]) >= 24:
            # GTFS allows departure_time like "25:30:00" for trips spanning
            # midnight as continuation of the previous service day. Migration
            # 0011 makes scheduled_time a TIME column which can't hold those;
            # drop the row + log.
            _log.warning(
                "static_join: skipping trip_id=%r seq=%s extended departure_time=%r",
                trip_id,
                stop_seq,
                sched,
            )
            skipped_extended += 1
            continue
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
    return rows
