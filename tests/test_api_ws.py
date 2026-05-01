import pytest
import asyncpg
import os
from fastapi.testclient import TestClient

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://localhost/transit")


@pytest.fixture
def ws_app_sync(apply_schema):
    import asyncio
    from api.main import app

    async def _setup():
        pool = await asyncpg.create_pool(DATABASE_URL)
        app.state.pool = pool
        row = await pool.fetchrow(
            "INSERT INTO agencies (agency_name, feed_url) VALUES ($1, $2) RETURNING agency_id",
            "WS Test Agency", "http://ws-test.example.com",
        )
        return pool, row["agency_id"]

    pool, agency_id = asyncio.get_event_loop().run_until_complete(_setup())
    yield app, agency_id, pool

    async def _teardown():
        async with pool.acquire() as conn:
            await conn.execute(
                "TRUNCATE agencies, updates, static_stops, static_stop_times, "
                "static_trips, static_routes, static_calendar_dates, "
                "agg_route_stats, agg_route_hour, agg_route_dow, "
                "agg_daily_trend, agg_stop_seq, rag_chunks CASCADE"
            )
        await pool.close()

    asyncio.get_event_loop().run_until_complete(_teardown())


def test_websocket_unknown_agency_closes(ws_app_sync):
    app, agency_id, pool = ws_app_sync
    with TestClient(app) as client:
        with client.websocket_connect("/api/99999/chat") as ws:
            # Send a message and expect error response then close
            ws.send_text("テスト")
            data = ws.receive_json()
            assert "error" in data
            assert "99999" in data["error"]
