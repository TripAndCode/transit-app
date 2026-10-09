"""Pure shaping and SQL behind the control board's run timeline.

Like ``api.admin_board``, everything here is a total function of its
arguments — no clock, no connection — so the router stays a thin
"fetch, degrade, shape" layer and the day boundaries are unit-testable.

The window is a **JST civil day**, because that is the day an operator means
by "today's runs": the pipeline itself buckets on JST (``gtfs_pipeline``
pins the session timezone), and a UTC window would split one night's
scheduled sweep across two pages of the timeline.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Mapping
from zoneinfo import ZoneInfo

_JST = ZoneInfo("Asia/Tokyo")

#: Kinds the manual trigger can actually perform. ``weather`` and ``static``
#: are recorded by the pipeline but have no admin-triggerable path, so
#: accepting them would answer 202 for work that never happens.
TRIGGERABLE_KINDS: tuple[str, ...] = ("ingest", "analyze")

RUNS_FOR_DAY_SQL = """
    SELECT r.run_id, r.kind, r.agency_id, a.agency_name, r.started_at, r.finished_at,
           r.status, r.rows, r.lock_wait_ms, r.error, r.requested_by
    FROM pipeline_runs r
    LEFT JOIN agencies a ON a.agency_id = r.agency_id
    WHERE r.started_at >= $1 AND r.started_at < $2
    ORDER BY r.started_at, r.run_id
"""

#: Opens the operator's run row and hands back the whole shaped row in one
#: statement, so the 202 can carry a real ``run_id`` and the timeline can draw
#: the bar immediately rather than waiting out a poll interval.
INSERT_MANUAL_RUN_SQL = """
    WITH inserted AS (
        INSERT INTO pipeline_runs (kind, agency_id, started_at, status, requested_by)
        VALUES ($1, $2, now(), 'running', $3)
        RETURNING run_id, kind, agency_id, started_at, finished_at, status, rows, lock_wait_ms, error, requested_by
    )
    SELECT i.run_id, i.kind, i.agency_id, a.agency_name, i.started_at, i.finished_at,
           i.status, i.rows, i.lock_wait_ms, i.error, i.requested_by
    FROM inserted i
    LEFT JOIN agencies a ON a.agency_id = i.agency_id
"""

LIVE_AGENCY_SQL = "SELECT agency_id FROM agencies WHERE agency_id = $1 AND deleted_at IS NULL"


def runs_day_bounds(day: date) -> tuple[datetime, datetime]:
    """The half-open UTC interval covering *day* in JST.

    Half-open on purpose: a run started exactly at midnight belongs to the day
    it opens, and to exactly one day.
    """
    start = datetime.combine(day, time.min, tzinfo=_JST)
    return start.astimezone(timezone.utc), (start + timedelta(days=1)).astimezone(timezone.utc)


def today_jst(now: datetime) -> date:
    """The civil day *now* falls on in JST."""
    return now.astimezone(_JST).date()


def _iso(value: Any) -> str | None:
    """A timestamp as a UTC ISO-8601 string, matching the collector tiles.

    A naive value is read as UTC rather than as the server's local time —
    the same rule ``api.admin_board`` applies — so a driver or a fake that
    hands back naive datetimes cannot silently shift a bar by the host's
    offset.
    """
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def shape_run(row: Mapping[str, Any]) -> dict[str, Any]:
    """One ``pipeline_runs`` row as the timeline reads it."""
    return {
        "run_id": row["run_id"],
        "kind": row["kind"],
        "agency_id": row["agency_id"],
        "agency_name": row["agency_name"],
        "started_at": _iso(row["started_at"]),
        "finished_at": _iso(row["finished_at"]),
        "status": row["status"],
        "rows": row["rows"],
        # The stored column is the cost of the non-blocking acquire attempt,
        # not time spent queueing -- there is no queue. Published under a
        # name that says so; the column keeps its historical one.
        "lock_probe_ms": row["lock_wait_ms"],
        "error": row["error"],
        "requested_by": row["requested_by"],
    }
