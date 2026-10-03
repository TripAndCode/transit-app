"""Agency catalogue reads shared by the public and admin listing endpoints.

Both endpoints list the same table in the same order and differ only in
whether soft-deleted rows are visible, so they share one query. It selects
the union of both callers' columns; each endpoint's response model names the
subset it publishes and drops the rest, so the admin fields never reach a
public caller.
"""

from __future__ import annotations

from typing import Any

# ``latest_data_date`` is a per-agency correlated subquery rather than a
# GROUP BY over agg_route_daily: the public listing is unauthenticated and
# frequently hit, and the correlated form takes an index-backed backward scan
# per agency instead of a full-table scan.
_LIST_SQL = """
SELECT a.agency_id, a.agency_name, a.feed_url, a.static_url,
       a.ingest_strategy, a.trip_id_pattern, a.deleted_at,
       (SELECT MAX(date) FROM agg_route_daily r WHERE r.agency_id = a.agency_id) AS latest_data_date
FROM agencies a
{where}ORDER BY a.agency_id
"""


def agency_row_to_dict(row) -> dict[str, Any]:
    """asyncpg hands back a raw ``datetime.date`` for ``latest_data_date`` (or
    ``None``); convert it explicitly to an ISO string rather than relying on
    Pydantic to coerce a date onto a str-typed field, matching the convention
    the report payloads already follow."""
    d = dict(row)
    if d.get("latest_data_date") is not None:
        d["latest_data_date"] = d["latest_data_date"].isoformat()
    return d


async def list_agencies(conn, *, include_deleted: bool) -> list[dict[str, Any]]:
    """Every agency, oldest id first. ``include_deleted`` keeps soft-deleted
    rows in the result — admin-only; the public catalogue never sees them."""
    where = "" if include_deleted else "WHERE a.deleted_at IS NULL\n"
    rows = await conn.fetch(_LIST_SQL.format(where=where))
    return [agency_row_to_dict(r) for r in rows]
