"""The retention prune's `day` range predicate has an index of its own: the
upsert arbiter leads with user_id, so it cannot serve a scan by day alone."""

import pytest


@pytest.mark.asyncio
async def test_user_activity_daily_has_a_day_index(aconn):
    indexdef = await aconn.fetchval(
        "SELECT indexdef FROM pg_indexes"
        " WHERE tablename = 'user_activity_daily' AND indexname = 'idx_user_activity_daily_day'"
    )
    assert indexdef is not None and "(day)" in indexdef
