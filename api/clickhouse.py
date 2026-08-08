"""Async ClickHouse client factory for the FastAPI app — the API-side
counterpart to pipeline/clickhouse.py's sync client. One shared client
for the process lifetime (clickhouse-connect's async client pools HTTP
connections internally), opened in api.main's lifespan and closed on
shutdown, same lifecycle shape as app.state.pool for Postgres.
"""

import os
from datetime import datetime, timezone

import clickhouse_connect


async def get_ch_client():
    """Caps any single query at 30s execution time and 200k result rows —
    the ClickHouse-side counterpart to api.main._init_connection's Postgres
    `statement_timeout`. All read endpoints serve from small precomputed
    agg_* tables (sub-second); ClickHouse only backs the pathological
    live-fallback scans over `updates` (see api.main.lifespan), so these
    caps should only ever fire as a safety net against a hung or
    runaway request — never on real traffic.

    `result_overflow_mode: "throw"` (not the default "break") matters as
    much as the row number itself: "break" truncates the result set
    silently and returns what it has so far, which would hand a caller
    wrong/incomplete data with no signal. "throw" raises a catchable
    DatabaseError (TOO_MANY_ROWS_OR_BYTES) instead, so an over-cap query
    fails loudly rather than returning a partial answer that looks correct.

    200_000 rows is generous headroom over this codebase's real query
    shapes: every live-fallback path is a single-agency, date-bounded scan
    over `updates` (per-stop/per-route rows for one agency's service day),
    which tops out in the thousands, not hundreds of thousands — see
    max_captured_at_before's docstring for how these queries are shaped to
    stay index-served. clickhouse-connect converts these to strings itself
    when posting settings over HTTP, so plain Python int/str values here are
    fine as written.
    """
    return await clickhouse_connect.get_async_client(
        host=os.environ.get("CLICKHOUSE_HOST", "localhost"),
        port=int(os.environ.get("CLICKHOUSE_PORT", "8123")),
        username=os.environ["CLICKHOUSE_USER"],
        password=os.environ["CLICKHOUSE_PASSWORD"],
        database=os.environ["CLICKHOUSE_DATABASE"],
        settings={
            "max_execution_time": 30,
            "max_result_rows": 200_000,
            "result_overflow_mode": "throw",
        },
    )


async def max_captured_at_before(ch, agency_id: int, before: datetime) -> datetime | None:
    """Async counterpart of `pipeline.clickhouse.max_captured_at_before`.

    Same index-served ``ORDER BY captured_at DESC LIMIT 1`` form (see that
    function's docstring for why this beats `maxOrNull`/an unfiltered
    `GROUP BY`): `captured_at` is the second column in `updates`' sort key
    `(agency_id, captured_at, route_code, trip_id, stop_sequence)`, so a
    single-agency, filtered-then-limited read is served off the sort index
    (reads ~thousands of rows) instead of scanning every row for the agency
    (or, worse, the whole table). Used by the async API call sites —
    `pipeline.health.aggregate_freshness` and
    `pipeline.reports.network.compute_network_summary` — that need one
    agency's latest-completed-day max at a time, looping over agencies in
    Python rather than a cross-agency `GROUP BY` (ClickHouse has no LATERAL
    join, and an unfiltered `GROUP BY agency_id` over `updates` reads the
    `captured_at` column for the entire table).
    """
    result = await ch.query(
        "SELECT captured_at FROM updates "
        "WHERE agency_id = {agency_id:UInt16} AND captured_at < {before:DateTime64} "
        "ORDER BY captured_at DESC LIMIT 1",
        parameters={"agency_id": agency_id, "before": before},
    )
    if not result.result_rows:
        return None
    value = result.result_rows[0][0]
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
