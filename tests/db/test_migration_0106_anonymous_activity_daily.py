"""anonymous_activity_daily merges counts per key, NULL agency included, and
keeps distinct agencies apart."""

import pytest

_UPSERT = """
    INSERT INTO anonymous_activity_daily (day, route, method, agency_id, requests, errors)
    VALUES (DATE '2026-01-05', '/api/agencies', 'GET', $1, 1, 0)
    ON CONFLICT (day, route, method, (COALESCE(agency_id, 0))) DO UPDATE SET
        requests = anonymous_activity_daily.requests + EXCLUDED.requests
"""


@pytest.mark.asyncio
async def test_a_null_agency_row_merges_instead_of_duplicating(aconn):
    await aconn.execute(_UPSERT, None)
    await aconn.execute(_UPSERT, None)
    rows = await aconn.fetch(
        "SELECT requests FROM anonymous_activity_daily WHERE route = '/api/agencies' AND agency_id IS NULL"
    )
    assert [r["requests"] for r in rows] == [2]


@pytest.mark.asyncio
async def test_distinct_agencies_do_not_collide(aconn):
    await aconn.execute(_UPSERT, 1)
    await aconn.execute(_UPSERT, 2)
    rows = await aconn.fetch(
        "SELECT agency_id, requests FROM anonymous_activity_daily"
        " WHERE route = '/api/agencies' AND agency_id IN (1, 2) ORDER BY agency_id"
    )
    assert [(r["agency_id"], r["requests"]) for r in rows] == [(1, 1), (2, 1)]
