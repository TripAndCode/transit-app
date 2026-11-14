"""The board's heatmap queries, against the real schema: both the joined read
and its plain-agency fallback name each agency's feed, so the heatmap groups
by upstream host on either path."""

import pytest

from api.admin_board import board_freshness
from api.routers import admin as admin_router
from tests.fixtures.clock import jst_today

pytestmark = pytest.mark.asyncio


async def test_both_heatmap_queries_carry_the_upstream_host(aconn):
    ours = {
        r["agency_id"]
        for r in await aconn.fetch(
            "INSERT INTO agencies (agency_name, feed_url) VALUES "
            "('Hiroden', 'https://ajt.mcapps.jp/realtime/8/t.bin'), ('Aomori', 'https://aomori.example.jp/rt') "
            "RETURNING agency_id"
        )
    }
    today = jst_today()
    for sql, args in (
        (admin_router._BOARD_FRESHNESS_SQL, (today, today)),
        (admin_router._BOARD_AGENCIES_SQL, ()),
    ):
        rows = await aconn.fetch(sql, *args)
        hosts = [(r["agency_name"], r["upstream_host"]) for r in board_freshness(rows, today) if r["agency_id"] in ours]
        assert hosts == [("Hiroden", "ajt.mcapps.jp"), ("Aomori", "aomori.example.jp")]
