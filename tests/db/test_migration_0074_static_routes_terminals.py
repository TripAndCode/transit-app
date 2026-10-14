"""static_routes carries each route's terminal and via stop names as optional
text and its loop/both-ways flags as booleans that default to false, so a
route row loaded without any of them stays valid."""

import pytest


@pytest.mark.asyncio
async def test_the_terminal_columns_are_nullable_text_and_the_flags_default_false(aconn):
    rows = await aconn.fetch(
        "SELECT column_name, data_type, is_nullable, column_default FROM information_schema.columns "
        "WHERE table_name = 'static_routes' "
        "AND column_name IN ('origin_name', 'via_name', 'destination_name', 'is_loop', 'bidirectional') "
        "ORDER BY column_name"
    )
    assert [tuple(r) for r in rows] == [
        ("bidirectional", "boolean", "NO", "false"),
        ("destination_name", "text", "YES", None),
        ("is_loop", "boolean", "NO", "false"),
        ("origin_name", "text", "YES", None),
        ("via_name", "text", "YES", None),
    ]


@pytest.mark.asyncio
async def test_a_route_row_without_terminals_still_inserts(aconn):
    await aconn.execute(
        "INSERT INTO agencies (agency_id, agency_name, feed_url) VALUES (8, 'Test Agency', 'https://example.test') "
        "ON CONFLICT DO NOTHING"
    )
    await aconn.execute("INSERT INTO static_routes (agency_id, route_id, route_short_name) VALUES (8, 'R1', '1')")
    row = await aconn.fetchrow(
        "SELECT origin_name, via_name, destination_name, is_loop, bidirectional "
        "FROM static_routes WHERE agency_id = 8 AND route_id = 'R1'"
    )
    assert tuple(row) == (None, None, None, False, False)
