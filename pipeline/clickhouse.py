"""Sync ClickHouse client + shared helpers for the `updates` table, used by
the ingest/analyze CLI paths (pipeline/ingest.py, pipeline/analyze.py,
pipeline/freshness.py). Mirrors pipeline/db.py's role for the Postgres side:
one place for the raw-`updates` SQL shape, so ingest and analyze can't drift.
"""

import os
from datetime import datetime, timezone

import clickhouse_connect

# Column order matches every ingest strategy's row-tuple shape (see
# pipeline/strategies/*.py parse_feed docstrings), minus agency_id which
# insert_updates prepends. Does NOT need to match db/clickhouse/schema.sql's
# column order (it doesn't: schema.sql declares captured_at before
# file_name) -- insert_updates always passes column_names=UPDATE_COLUMNS
# explicitly, so clickhouse-connect maps by name, not position.
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
        # Explicit knob rather than relying on clickhouse-connect's port-based
        # https inference (only triggers on port 443/8443): without either,
        # this client sends CLICKHOUSE_PASSWORD as HTTP Basic auth in
        # cleartext on every ingest/analyze/bootstrap request.
        secure=os.environ.get("CLICKHOUSE_SECURE", "false").lower() in ("1", "true", "yes"),
    )


def insert_updates(client, agency_id: int, rows: list[tuple]) -> int:
    """Prepend `agency_id` to each row and bulk-insert into `updates`.

    No ON CONFLICT equivalent at the database level — see the design doc's
    dedup/idempotency section. File-level idempotency (distinct_file_names,
    checked by the caller before parsing) is the real duplicate guard against
    RE-INGESTING a file. Within a single call, dedup by (file_name, trip_id,
    stop_sequence) here, first-occurrence-wins, matching Postgres's old
    UNIQUE(agency_id, file_name, trip_id, stop_sequence) + ON CONFLICT DO
    NOTHING exactly: a GTFS-RT feed occasionally repeats a TripUpdate entity
    or a stop_sequence within one poll (both ingest strategies iterate every
    entity/stop_time_update with no such check), and while that self-heals
    for argMax-based dedup reads, it silently double-counts every raw
    COUNT(*) consumer (agg_feed_health.raw_samples, describe_data's
    total_rows/observations) that Postgres never had to guard against.
    """
    seen: set[tuple] = set()
    ch_rows = []
    for r in rows:
        key = (r[0], r[2], r[6])  # (file_name, trip_id, stop_sequence)
        if key in seen:
            continue
        seen.add(key)
        ch_rows.append((agency_id, *r))
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


def recent_file_name_exists(client, agency_id: int, file_name: str, since: datetime) -> bool:
    """Bounded existence check for one specific `file_name` — for callers
    (ingest_live) that only ever need to ask about a file just constructed
    from `now()`, where distinct_file_names' unbounded per-agency scan would
    be wasteful to pay on every single poll. `since` keeps this index-served
    off the `(agency_id, captured_at, ...)` sort key rather than forcing a
    full-partition scan for the `file_name` predicate alone.
    """
    result = client.query(
        "SELECT 1 FROM updates WHERE agency_id = {agency_id:UInt16} "
        "AND captured_at >= {since:DateTime64} AND file_name = {file_name:String} LIMIT 1",
        parameters={"agency_id": agency_id, "since": since, "file_name": file_name},
    )
    return bool(result.result_rows)


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
        "SELECT captured_at FROM updates WHERE agency_id = {agency_id:UInt16} ORDER BY captured_at DESC LIMIT 1",
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
