"""The manual ridership-weight table and the ridership-weighted on-time rate
it powers.

No IC-card or APC ridership data source exists in this pipeline, so
``ridership_weights`` (migration 0035) is a small, manually-populated
external table: an operator supplies a RELATIVE weight per agency, optionally
narrowed to one route (see the migration's own docstring for the
route_code-NULL-is-default convention). When configured, a caller can pool
each route's on-time contribution by that weight instead of counting every
stop-event equally -- e.g. a high-ridership route's performance dominates the
headline rate the way it would if real passenger counts existed, instead of
every route (regardless of how few riders it carries) pulling the rate by the
same amount per stop-event.

An agency with NO rows in ``ridership_weights`` at all has no ridership
weighting configured. :func:`compute_ridership_weighted_on_time_by_agency`
omits that agency from its returned dict entirely -- never a silent uniform
weight of 1 applied everywhere and mislabeled "weighted" -- so a caller (see
``api/routers/network.py``) can hide/disable a weighted-view toggle rather
than rendering a no-op view that's identical to the unweighted one.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

# Network-board variant: one pass across every already-configured agency
# (GROUP BY agency_id) instead of N per-agency round trips, matching
# pipeline.reports.network.compute_network_summary's own one-query-per-source
# shape. Bound to the configured agency_id set (fetched first, see
# compute_ridership_weighted_on_time_by_agency) rather than scanning every
# agency's agg_route_daily_dist rows: ridership weighting is a sparse,
# manually-populated opt-in, so most agencies would otherwise be weighted and
# summed here only to be discarded.
_WEIGHTED_BY_AGENCY_SQL = """
    SELECT d.agency_id,
        SUM(d.on_time_count * COALESCE(rw_route.weight, rw_default.weight, 1))::numeric AS weighted_on_time,
        SUM(d.samples * COALESCE(rw_route.weight, rw_default.weight, 1))::numeric AS weighted_samples
    FROM agg_route_daily_dist d
    LEFT JOIN ridership_weights rw_route
        ON rw_route.agency_id = d.agency_id AND rw_route.route_code = d.route_code
    LEFT JOIN ridership_weights rw_default
        ON rw_default.agency_id = d.agency_id AND rw_default.route_code IS NULL
    WHERE d.date BETWEEN $1 AND $2 AND d.agency_id = ANY($3::int[])
    GROUP BY d.agency_id
"""

_AGENCIES_WITH_WEIGHTS_SQL = "SELECT DISTINCT agency_id FROM ridership_weights"


def _pct(
    weighted_on_time: Decimal | int | float | None, weighted_samples: Decimal | int | float | None
) -> float | None:
    """Weighted on-time percentage, or None for a zero/absent denominator
    (no samples in range -- same "not available" convention as
    compute_network_summary's own unweighted on_time_pct)."""
    if not weighted_samples:
        return None
    return round(float(weighted_on_time or 0) / float(weighted_samples) * 100, 1)


async def compute_ridership_weighted_on_time_by_agency(
    conn, from_date: date, to_date: date
) -> dict[int, float | None]:
    """Per-agency ridership-weighted on-time percentage over [from_date,
    to_date], for every agency that has at least one ridership_weights row.

    Returns a dict keyed by agency_id; an agency with no configured weights
    is simply absent from the returned dict (not present with a None or a
    misleading 1-weighted value) -- callers (see
    pipeline.reports.network.compute_network_summary) should treat a missing
    key exactly like "not configured".
    """
    configured = {r["agency_id"] for r in await conn.fetch(_AGENCIES_WITH_WEIGHTS_SQL)}
    if not configured:
        return {}
    rows = await conn.fetch(_WEIGHTED_BY_AGENCY_SQL, from_date, to_date, list(configured))
    by_agency = {r["agency_id"]: _pct(r["weighted_on_time"], r["weighted_samples"]) for r in rows}
    # An agency with configured weights but zero agg_route_daily_dist rows in
    # range has no row in `rows` at all (the LEFT JOINs are anchored on d,
    # the aggregate table) -- still "configured", just no data for this
    # range, so it's included here as None rather than silently omitted.
    return {aid: by_agency.get(aid) for aid in configured}
