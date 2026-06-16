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
