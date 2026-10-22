"""Lookups by `login_events.actor_id` (the account export, erasure, and the
FK's ON DELETE SET NULL) have an index of their own: no other index on the
table covers actor_id."""

import pytest


@pytest.mark.asyncio
async def test_login_events_has_a_partial_actor_index(aconn):
    indexdef = await aconn.fetchval(
        "SELECT indexdef FROM pg_indexes"
        " WHERE tablename = 'login_events' AND indexname = 'idx_login_events_actor_created'"
    )
    assert indexdef is not None
    assert "(actor_id, created_at DESC)" in indexdef
    assert indexdef.endswith("WHERE (actor_id IS NOT NULL)")
