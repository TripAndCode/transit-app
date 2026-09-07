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
weighting configured. :func:`compute_ridership_weighted_on_time` returns
``None`` for that agency -- never a silent uniform weight of 1 applied
everywhere and mislabeled "weighted" -- so a caller (see
``api/routers/network.py``) can hide/disable a weighted-view toggle rather
than rendering a no-op view that's identical to the unweighted one.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

_HAS_WEIGHTS_SQL = "SELECT 1 FROM ridership_weights WHERE agency_id = $1 LIMIT 1"

# Each row's weight resolves to its own route-specific row, else the
# agency's default (route_code IS NULL) row, else 1 -- distinct from having
# NO ridership_weights rows at all for this agency, which
# agency_has_ridership_weights gates on before this query runs.
# agg_route_daily_dist is already a per-day aggregate, so weighting each row
# directly (no pre-grouping by route) is exact: weight is constant per route.
_WEIGHTED_SQL = """
    SELECT
        SUM(d.on_time_count * COALESCE(rw_route.weight, rw_default.weight, 1))::numeric AS weighted_on_time,
        SUM(d.samples * COALESCE(rw_route.weight, rw_default.weight, 1))::numeric AS weighted_samples
    FROM agg_route_daily_dist d
    LEFT JOIN ridership_weights rw_route
        ON rw_route.agency_id = d.agency_id AND rw_route.route_code = d.route_code
    LEFT JOIN ridership_weights rw_default
        ON rw_default.agency_id = d.agency_id AND rw_default.route_code IS NULL
    WHERE d.agency_id = $1 AND d.date BETWEEN $2 AND $3
"""

# Network-board variant of _WEIGHTED_SQL: one pass across every agency
# (GROUP BY agency_id) instead of N per-agency round trips, matching
# pipeline.reports.network.compute_network_summary's own one-query-per-source
# shape.
_WEIGHTED_BY_AGENCY_SQL = """
    SELECT d.agency_id,
        SUM(d.on_time_count * COALESCE(rw_route.weight, rw_default.weight, 1))::numeric AS weighted_on_time,
        SUM(d.samples * COALESCE(rw_route.weight, rw_default.weight, 1))::numeric AS weighted_samples
    FROM agg_route_daily_dist d
    LEFT JOIN ridership_weights rw_route
        ON rw_route.agency_id = d.agency_id AND rw_route.route_code = d.route_code
    LEFT JOIN ridership_weights rw_default
        ON rw_default.agency_id = d.agency_id AND rw_default.route_code IS NULL
    WHERE d.date BETWEEN $1 AND $2
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


async def agency_has_ridership_weights(conn, agency_id: int) -> bool:
    """Whether *agency_id* has ANY configured ridership-weight row (a
    route-specific row, an agency-default row, or both). The one authority
    for whether a caller may compute/show a ridership-weighted view for this
    agency at all -- see this module's own docstring for why "some rows
    exist" and "a uniform weight of 1 everywhere" must never be conflated.
    """
    row = await conn.fetchrow(_HAS_WEIGHTS_SQL, agency_id)
    return row is not None


async def compute_ridership_weighted_on_time(
    conn, agency_id: int, from_date: date, to_date: date
) -> float | None:
    """Ridership-weighted on-time percentage over [from_date, to_date].

    Reads agg_route_daily_dist's exact on_time_count/samples columns (the
    legacy_60s on-time definition baked in at analyze time -- same scope as
    pipeline.reports.network.compute_network_summary's own unweighted
    on_time_pct), weighting each route's contribution by its resolved
    ridership_weights row.

    Returns None when *agency_id* has no ridership_weights rows configured
    at all (see agency_has_ridership_weights) -- never a value computed with
    an implicit weight of 1 for every route silently standing in for "no
    configuration".
    """
    if not await agency_has_ridership_weights(conn, agency_id):
        return None
    row = await conn.fetchrow(_WEIGHTED_SQL, agency_id, from_date, to_date)
    if row is None:
        return None
    return _pct(row["weighted_on_time"], row["weighted_samples"])


async def compute_ridership_weighted_on_time_by_agency(
    conn, from_date: date, to_date: date
) -> dict[int, float | None]:
    """Per-agency ridership-weighted on-time percentage over [from_date,
    to_date], for every agency that has at least one ridership_weights row.

    Returns a dict keyed by agency_id; an agency with no configured weights
    is simply absent from the returned dict (not present with a None or a
    misleading 1-weighted value) -- callers (see
    pipeline.reports.network.compute_network_summary) should treat a missing
    key exactly like agency_has_ridership_weights returning False for that
    agency.
    """
    configured = {r["agency_id"] for r in await conn.fetch(_AGENCIES_WITH_WEIGHTS_SQL)}
    if not configured:
        return {}
    rows = await conn.fetch(_WEIGHTED_BY_AGENCY_SQL, from_date, to_date)
    by_agency = {r["agency_id"]: _pct(r["weighted_on_time"], r["weighted_samples"]) for r in rows}
    # An agency with configured weights but zero agg_route_daily_dist rows in
    # range has no row in `rows` at all (the LEFT JOINs are anchored on d,
    # the aggregate table) -- still "configured", just no data for this
    # range, so it's included here as None rather than silently omitted.
    return {aid: by_agency.get(aid) for aid in configured}
