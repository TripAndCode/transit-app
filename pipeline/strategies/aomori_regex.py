"""Aomori RT ingest strategy.

Decodes a TripUpdate pb into rows matching pipeline.clickhouse.insert_updates.
The trip_id regex (provided per agency in DB column trip_id_pattern, defaulting
to the Aomori format) carries route_code, service_type, and scheduled_time.

This strategy expects rows where the regex matches; non-matching trip_ids are
dropped (preserving today's Aomori ingest behaviour).
"""

import logging
import re

from pipeline.strategies._pb import _dec, _fields
from pipeline.strategies._time import normalize_departure_time

_log = logging.getLogger(__name__)

_TRIP_RE_DEFAULT = re.compile(r"^(?P<service>.+?)_(?P<hour>\d+)時(?P<minute>\d+)分_系統(?P<route>\d+)$")


def _resolve_pattern(agency_id: int, conn) -> re.Pattern:
    """Fetch the agency's trip_id_pattern from DB, falling back to the Aomori default."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT trip_id_pattern FROM agencies WHERE agency_id = %s",
            (agency_id,),
        )
        row = cur.fetchone()
    if row and row[0]:
        return re.compile(row[0])
    return _TRIP_RE_DEFAULT


def parse_trip_id(trip_id: str, pattern: re.Pattern = _TRIP_RE_DEFAULT) -> dict | None:
    """Match trip_id against pattern and return its named groups, or None on no match."""
    m = pattern.match(trip_id)
    return m.groupdict() if m else None


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
    The 9-tuple consumed by INSERT prepends agency_id at insert time.
    """
    pattern = _resolve_pattern(agency_id, conn)
    rows: list[tuple] = []
    try:
        top = _fields(pb_bytes)
    except Exception:
        return rows
    for ent_bytes in top.get(2, []):
        ent = _fields(ent_bytes)
        if 3 not in ent:
            continue
        tu = _fields(ent[3][0])
        trip_id = None
        if 1 in tu:
            trip = _fields(tu[1][0])
            if 1 in trip:
                trip_id = _dec(trip[1][0])
        if not trip_id:
            continue
        parsed = parse_trip_id(trip_id, pattern=pattern)
        if parsed is None:
            continue
        service = parsed.get("service")
        hour = parsed.get("hour", "")
        minute = parsed.get("minute", "")
        sched = None
        if hour and minute:
            # trip_id_pattern is per-agency, admin-editable (agencies.trip_id_
            # pattern) -- an unguarded int(hour)/int(minute) on its named
            # groups raises ValueError for a pattern whose hour/minute group
            # can match non-digits, taking out the whole feed's batch. Route
            # through the same validator static_join.py uses: zfill first
            # (regex groups aren't guaranteed 2 digits, e.g. hour="9"), then
            # let normalize_departure_time reject anything that still isn't
            # a clean "HH:MM" shape rather than crash on it.
            sched, status = normalize_departure_time(f"{hour.zfill(2)}:{minute.zfill(2)}")
            if status in ("extended", "bad"):
                # Strict TIME column (migration 0011) rejected extended-hour
                # and invalid-minute values; the column is Nullable(String)
                # now, but every downstream read site still assumes a
                # same-day HH:MM shape, so skip the whole trip's
                # stop_time_updates rather than store something meaningless.
                _log.warning("aomori: skipping trip_id %r with invalid time %s:%s", trip_id, hour, minute)
                continue
        route = parsed.get("route")
        for stu_bytes in tu.get(2, []):
            stu = _fields(stu_bytes)
            stop_seq = stu.get(1, [None])[0]
            dep_delay = None
            if 3 in stu:
                dep = _fields(stu[3][0])
                dep_delay = dep.get(1, [None])[0]
            rows.append((file_name, captured_at, trip_id, service, sched, route, stop_seq, dep_delay))
    return rows
