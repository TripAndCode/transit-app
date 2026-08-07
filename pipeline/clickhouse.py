"""Sync ClickHouse client + shared helpers for the `updates` table, used by
the ingest/analyze CLI paths (pipeline/ingest.py, pipeline/analyze.py,
pipeline/freshness.py). Mirrors pipeline/db.py's role for the Postgres side:
one place for the raw-`updates` SQL shape, so ingest and analyze can't drift.
"""

import os
from datetime import datetime, timezone

import clickhouse_connect

# Column order matches every ingest strategy's row-tuple shape (see
# pipeline/strategies/*.py parse_feed docstrings) plus the schema in
# db/clickhouse/schema.sql, minus agency_id which insert_updates prepends.
UPDATE_COLUMNS = [
    "agency_id",
    "file_name",
    "captured_at",
    "trip_id",
    "service_type",
    "scheduled_time",
    "route_code",
    "stop_sequence",
    "dep_delay",
]


def get_client():
    return clickhouse_connect.get_client(
        host=os.environ.get("CLICKHOUSE_HOST", "localhost"),
        port=int(os.environ.get("CLICKHOUSE_PORT", "8123")),
        username=os.environ["CLICKHOUSE_USER"],
        password=os.environ["CLICKHOUSE_PASSWORD"],
        database=os.environ["CLICKHOUSE_DATABASE"],
    )


def insert_updates(client, agency_id: int, rows: list[tuple]) -> int:
    """Prepend `agency_id` to each row and bulk-insert into `updates`.

    No ON CONFLICT equivalent — see the design doc's dedup/idempotency
    section. File-level idempotency (distinct_file_names, checked by the
    caller before parsing) is the real duplicate guard; any rare
    intra-file duplicate self-heals at dedup-query read time.
    """
    ch_rows = [(agency_id, *r) for r in rows]
    if not ch_rows:
        return 0
    summary = client.insert("updates", ch_rows, column_names=UPDATE_COLUMNS)
    return summary.written_rows


def distinct_file_names(client, agency_id: int) -> set[str]:
    result = client.query(
        "SELECT DISTINCT file_name FROM updates WHERE agency_id = {agency_id:UInt16}",
        parameters={"agency_id": agency_id},
    )
    return {row[0] for row in result.result_rows}


def max_captured_at(client, agency_id: int) -> datetime | None:
    """Absolute latest `captured_at` for the agency, today included.

    For "is this feed still alive at all" checks (e.g. `/delays/live`'s
    freshness header). For "what's the latest COMPLETED day" checks, use
    `max_captured_at_before` instead — do not add a same-day exclusion here,
    it would silently change this function's meaning for existing callers.

    Uses `ORDER BY captured_at DESC LIMIT 1` rather than `maxOrNull(captured_at)`:
    `captured_at` is the SECOND column in `updates`' `ORDER BY (agency_id,
    captured_at, route_code, trip_id, stop_sequence)` sort key, so this form is
    served directly off the sort index (reads ~thousands of rows), while
    `maxOrNull` is a full per-agency aggregate scan that reads every row for
    the agency. Same result either way — an empty result set (no rows for
    `agency_id`) means "no latest row", matching `maxOrNull`'s `None`.
    """
    result = client.query(
        "SELECT captured_at FROM updates WHERE agency_id = {agency_id:UInt16} "
        "ORDER BY captured_at DESC LIMIT 1",
        parameters={"agency_id": agency_id},
    )
    if not result.result_rows:
        return None
    value = result.result_rows[0][0]
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def max_captured_at_before(client, agency_id: int, before: datetime) -> datetime | None:
    """Latest `captured_at` strictly before `before` (a tz-aware UTC cutoff).

    For "what's the latest COMPLETED day" freshness checks: filtering rows
    BEFORE taking the max means the result already IS the latest completed
    day's max, with no further accept/reject needed by the caller — unlike
    calling `max_captured_at` and then checking `< before` in Python, which
    would incorrectly return "no completed day" whenever a later (e.g.
    today's, still-ingesting) row also exists, instead of falling back to
    the latest prior completed day.

    Same `ORDER BY captured_at DESC LIMIT 1` index-served form as
    `max_captured_at` (see its docstring) instead of `maxOrNull` — the
    `captured_at < {before}` predicate composes fine with the sort-key scan.
    """
    result = client.query(
        "SELECT captured_at FROM updates "
        "WHERE agency_id = {agency_id:UInt16} AND captured_at < {before:DateTime64} "
        "ORDER BY captured_at DESC LIMIT 1",
        parameters={"agency_id": agency_id, "before": before},
    )
    if not result.result_rows:
        return None
    value = result.result_rows[0][0]
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
