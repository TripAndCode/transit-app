"""Async ClickHouse client factory for the FastAPI app — the API-side
counterpart to pipeline/clickhouse.py's sync client. One shared client
for the process lifetime (clickhouse-connect's async client pools HTTP
connections internally), opened in api.main's lifespan and closed on
shutdown, same lifecycle shape as app.state.pool for Postgres.
"""

import asyncio
import logging
from datetime import datetime, timezone

import clickhouse_connect

from pipeline.clickhouse import ch_conn_kwargs


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
        **ch_conn_kwargs(),
        settings={
            "max_execution_time": 30,
            "max_result_rows": 200_000,
            "result_overflow_mode": "throw",
            # This process only ever SELECTs from `updates` — every write/DDL
            # path (ingest, analyze, bootstrap) goes through pipeline/clickhouse.py's
            # sync client instead. `readonly=2` (not 1) so the settings above
            # can still be applied per-query; `readonly=1` would reject those
            # settings outright. Applied as a query-level setting on every
            # request THIS CLIENT OBJECT issues (clickhouse-connect's async
            # client is sessionless by default, so there's no server-side
            # session to attach it to) -- it holds for all traffic through
            # this object, but a call site that explicitly passes its own
            # `readonly` in `settings={...}` to `ch.query(...)` can still
            # lift it (an override only wins for keys it actually contains).
            # A defense-in-depth default, not a substitute for a genuinely
            # read-only CH user/profile if one is provisioned later.
            "readonly": 2,
        },
    )


async def max_captured_at(ch, agency_id: int) -> datetime | None:
    """Async counterpart of `pipeline.clickhouse.max_captured_at`.

    Absolute latest `captured_at` for the agency (today included) — see that
    function's docstring for why the index-served `ORDER BY captured_at DESC
    LIMIT 1` form beats `maxOrNull`. Agency-scoped only (no route/date
    filter), so it stays servable off the sort key's leading `agency_id`
    column regardless of how the caller intends to use the result — callers
    that need a *route*-scoped probe (e.g. `api.routers.map`'s `route_trips`,
    `route_stop_profile`, `route_shape`) should call this first and derive a
    literal Python-computed lower bound from it, rather than filtering
    `updates` by `route_code` with no date bound: `route_code` sits behind
    the unconstrained `captured_at` in the sort key, so an unbounded
    route-scoped query can't be pruned and costs a full-table scan whenever
    the route doesn't exist or has no recent data.
    """
    result = await ch.query(
        "SELECT captured_at FROM updates WHERE agency_id = {agency_id:UInt16} ORDER BY captured_at DESC LIMIT 1",
        parameters={"agency_id": agency_id},
    )
    if not result.result_rows:
        return None
    value = result.result_rows[0][0]
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


async def max_captured_at_before(ch, agency_id: int, before: datetime) -> datetime | None:
    """Async counterpart of `pipeline.clickhouse.max_captured_at_before`.

    Same index-served ``ORDER BY captured_at DESC LIMIT 1`` form (see that
    function's docstring for why this beats `maxOrNull`/an unfiltered
    `GROUP BY`): `captured_at` is the second column in `updates`' sort key
    `(agency_id, captured_at, route_code, trip_id, stop_sequence)`, so a
    single-agency, filtered-then-limited read is served off the sort index
    (reads ~thousands of rows) instead of scanning every row for the agency
    (or, worse, the whole table). Called per-agency by
    `max_captured_at_before_by_agency` below (used by
    `pipeline.health.aggregate_freshness` and
    `pipeline.reports.network.compute_network_summary`) rather than a
    cross-agency `GROUP BY` (ClickHouse has no LATERAL join, and an
    unfiltered `GROUP BY agency_id` over `updates` reads the `captured_at`
    column for the entire table).
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


async def min_captured_at(ch, agency_id: int) -> datetime | None:
    """Earliest `captured_at` ever recorded for an agency.

    Same index-served single-row-read shape as `max_captured_at` (see its
    docstring), just `ORDER BY captured_at ASC`: `captured_at` is the second
    column in `updates`' sort key, so this is served off the sort index
    rather than a table scan regardless of direction. Used only for
    agencies with zero `agg_route_daily` rows (never analyzed), to size
    `pipeline.health.aggregate_freshness`'s `agg_behind_days` off the actual
    span of unaggregated data instead of a hardcoded placeholder — so it
    stays a rare, single-agency probe rather than a query run for every
    agency on every call.
    """
    result = await ch.query(
        "SELECT captured_at FROM updates WHERE agency_id = {agency_id:UInt16} ORDER BY captured_at ASC LIMIT 1",
        parameters={"agency_id": agency_id},
    )
    if not result.result_rows:
        return None
    value = result.result_rows[0][0]
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


async def min_captured_at_by_agency(ch, agency_ids: list[int], log: logging.Logger) -> dict[int, datetime | None]:
    """Per-agency `min_captured_at`, run concurrently.

    Same degrade-on-failure shape as `max_captured_at_before_by_agency`: a
    failing probe degrades that one agency to `None` rather than failing the
    whole batch. Callers pass only the (normally small) subset of agencies
    with no `agg_route_daily` rows at all, since that's the only case this
    backs.
    """

    async def _probe(aid: int) -> tuple[int, datetime | None]:
        try:
            return aid, await min_captured_at(ch, aid)
        except Exception:
            log.warning("ClickHouse earliest-day probe failed for agency %s — degrading", aid, exc_info=True)
            return aid, None

    return dict(await asyncio.gather(*(_probe(aid) for aid in agency_ids)))


async def max_captured_at_before_by_agency(
    ch, agency_ids: list[int], before: datetime, log: logging.Logger
) -> dict[int, datetime | None]:
    """Per-agency `max_captured_at_before`, run concurrently.

    Shared by `pipeline.health.aggregate_freshness` and
    `pipeline.reports.network.compute_network_summary`, which both need this
    exact shape: N independent per-agency freshness probes on one shared
    async client, backing only ONE non-critical field in a larger response —
    so a failing agency's probe must degrade that agency to `None` rather
    than fail the whole batch (`is_stale(agg_day, None)` is defined as "not
    stale" — the correct degrade). Concurrent because these are independent
    round trips on the same client, so running them serially would cost
    roughly the sum of every agency's probe instead of the slowest one;
    `gather` never raises here since each probe catches its own failure
    internally.
    """

    async def _probe(aid: int) -> tuple[int, datetime | None]:
        try:
            return aid, await max_captured_at_before(ch, aid, before)
        except Exception:
            log.warning("ClickHouse freshness probe failed for agency %s — degrading", aid, exc_info=True)
            return aid, None

    return dict(await asyncio.gather(*(_probe(aid) for aid in agency_ids)))
