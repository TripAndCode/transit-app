"""Async ClickHouse client factory for the FastAPI app — the API-side
counterpart to pipeline/clickhouse.py's sync client. One shared client
for the process lifetime (clickhouse-connect's async client pools HTTP
connections internally), opened in api.main's lifespan and closed on
shutdown, same lifecycle shape as app.state.pool for Postgres.
"""

import os

import clickhouse_connect


async def get_ch_client():
    return await clickhouse_connect.get_async_client(
        host=os.environ.get("CLICKHOUSE_HOST", "localhost"),
        port=int(os.environ.get("CLICKHOUSE_PORT", "8123")),
        username=os.environ["CLICKHOUSE_USER"],
        password=os.environ["CLICKHOUSE_PASSWORD"],
        database=os.environ["CLICKHOUSE_DATABASE"],
    )
