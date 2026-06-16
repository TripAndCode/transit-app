"""Detect stale or partially-built aggregate tables.

Read endpoints serve precomputed agg_* tables, never live scans, so an agency
whose analyze never ran (or whose cron loop crashed mid-run) silently serves
stale data. This module compares, per agency, the newest COMPLETED civil day in
the live `updates` fact table against the newest day materialised in
`agg_route_daily` — the canonical always-built agg. Today's partial day is
excluded so the continuously-lagging current day never trips the check.

All day arithmetic is pinned to Asia/Tokyo via explicit AT TIME ZONE, matching
how analyze() buckets `captured_at::date`. This keeps the check correct
regardless of the caller's session timezone — the cron path
(api/routers/internal.py) connects without pinning JST.
"""

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class StaleAgency:
    agency_id: int
    agg_max_day: date | None
    live_max_completed_day: date


def is_stale(agg_max_day: "date | None", live_max_completed_day: "date | None") -> bool:
    """Pure staleness rule. Stale iff a completed day is owed but uncovered.

    - No completed days yet (live_max_completed_day is None): nothing owed → fresh.
    - Aggs empty (agg_max_day is None) but a completed day exists: stale.
    - Aggs lag the newest completed day: stale.
    """
    if live_max_completed_day is None:
        return False
    if agg_max_day is None:
        return True
    return agg_max_day < live_max_completed_day


# Newest COMPLETED JST civil day for an agency. Uses MAX(captured_at) (index-
# friendly on idx_updates_agency_at) bounded to instants strictly before JST
# midnight today, then converts that instant to its JST date. Returns None when
# the agency has no rows before today.
_LIVE_MAX_COMPLETED_SQL = """
    SELECT (MAX(captured_at) AT TIME ZONE 'Asia/Tokyo')::date
    FROM updates
    WHERE agency_id = %s
      AND captured_at < (date_trunc('day', now() AT TIME ZONE 'Asia/Tokyo'))
                        AT TIME ZONE 'Asia/Tokyo'
"""

_AGG_MAX_SQL = "SELECT MAX(date) FROM agg_route_daily WHERE agency_id = %s"


def check_agg_freshness(conn, agency_ids) -> list[StaleAgency]:
    """Return agencies whose aggregates lag their newest completed civil day.

    Read-only. Empty list means every agency is fresh.
    """
    stale: list[StaleAgency] = []
    with conn.cursor() as cur:
        for aid in agency_ids:
            cur.execute(_LIVE_MAX_COMPLETED_SQL, (aid,))
            live_max = cur.fetchone()[0]
            cur.execute(_AGG_MAX_SQL, (aid,))
            agg_max = cur.fetchone()[0]
            if is_stale(agg_max, live_max):
                stale.append(StaleAgency(aid, agg_max, live_max))
    return stale
