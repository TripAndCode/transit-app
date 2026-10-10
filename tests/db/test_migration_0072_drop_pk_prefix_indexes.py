"""An index that is a leading prefix of its table's primary key adds write cost
and no read benefit, so the schema carries neither of these."""

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize("indexname", ["idx_agg_route_stop_daily_agency_route", "idx_sst_trip"])
async def test_pk_prefix_index_is_absent(aconn, indexname):
    assert await aconn.fetchval("SELECT 1 FROM pg_indexes WHERE indexname = $1", indexname) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("table", "leading"),
    [
        ("agg_route_stop_daily", ["agency_id", "route_code"]),
        ("static_stop_times", ["agency_id", "trip_id"]),
    ],
)
async def test_primary_key_still_leads_with_the_dropped_prefix(aconn, table, leading):
    cols = await aconn.fetchval(
        """
        SELECT array_agg(a.attname::text ORDER BY k.ord)
        FROM pg_index i
        CROSS JOIN LATERAL unnest(i.indkey) WITH ORDINALITY AS k(attnum, ord)
        JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = k.attnum
        WHERE i.indrelid = $1::regclass AND i.indisprimary
        """,
        table,
    )
    assert cols[: len(leading)] == leading
