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

from collections.abc import Iterable, Mapping
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from pipeline.runs import DEFAULT_REAP_AGE

_JST = ZoneInfo("Asia/Tokyo")

#: Kinds the board's manual trigger can actually perform. ``weather`` has no
#: admin-triggerable path, and a timetable reload (``static``, ``update``) is
#: per agency, from the agencies page, so accepting them here would answer
#: 202 for work this trigger never does.
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

#: The steps an operator's run is read as, in the order the job performs them,
#: keyed by its umbrella row's kind. Promotion is not a step of its own: it is
#: the second half of getting realtime data in, so ``promote`` rows count
#: towards ``ingest`` (see ``_STEP_KINDS``).
RUN_PLANS: dict[str, tuple[str, ...]] = {
    "update": ("static", "ingest", "analyze"),
    "static": ("static",),
    "ingest": ("ingest", "analyze"),
    "analyze": ("analyze",),
}

#: The row kinds each step is made of. A step is done only once every one of
#: them has closed: between the ingest row closing and the promotion row
#: opening, the step is still in progress.
_STEP_KINDS: dict[str, tuple[str, ...]] = {
    "static": ("static",),
    "ingest": ("ingest", "promote"),
    "analyze": ("analyze",),
}

#: Takes the per-agency request lock (``pipeline.locks``) for the rest of the
#: caller's transaction.
AGENCY_RUN_REQUEST_LOCK_SQL = "SELECT pg_advisory_xact_lock($1, $2)"

#: Any job still working on this agency -- an operator's run, or a cron or
#: command-line one -- counts: a second run started beside it would race it
#: for the ingest/analyze lock, or reload the timetable under its analyze.
#: ``$2`` is :func:`in_flight_cutoff`, so a row whose process died is not
#: mistaken for work in progress for longer than the reaper would leave it.
AGENCY_RUN_IN_FLIGHT_SQL = """
    SELECT 1 FROM pipeline_runs
    WHERE agency_id = $1 AND status = 'running' AND finished_at IS NULL AND started_at > $2
    LIMIT 1
"""

AGENCIES_IN_FLIGHT_SQL = """
    SELECT DISTINCT agency_id FROM pipeline_runs
    WHERE agency_id IS NOT NULL AND status = 'running' AND finished_at IS NULL AND started_at > $1
"""

#: Each agency's newest operator-triggered umbrella row, and every step row
#: filed under it.
LATEST_OPERATOR_RUNS_SQL = """
    WITH latest AS (
        SELECT DISTINCT ON (agency_id) run_id
        FROM pipeline_runs
        WHERE parent_run_id IS NULL AND requested_by IS NOT NULL AND agency_id IS NOT NULL
        ORDER BY agency_id, started_at DESC, run_id DESC
    )
    SELECT r.run_id, r.parent_run_id, r.kind, r.agency_id, r.started_at, r.finished_at,
           r.status, r.rows, r.error
    FROM pipeline_runs r
    WHERE r.run_id IN (SELECT run_id FROM latest) OR r.parent_run_id IN (SELECT run_id FROM latest)
    ORDER BY r.started_at, r.run_id
"""

#: When each agency's timetable last loaded: its newest successful ``static``
#: run, from the agencies page or the command line.
LAST_STATIC_LOAD_SQL = """
    SELECT DISTINCT ON (agency_id) agency_id, finished_at
    FROM pipeline_runs
    WHERE kind = 'static' AND status = 'ok' AND agency_id IS NOT NULL
    ORDER BY agency_id, finished_at DESC
"""


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


def jst_date(value: Any) -> str | None:
    """The JST civil day a timestamp falls on, as ``YYYY-MM-DD``."""
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(_JST).date().isoformat()


def in_flight_cutoff(now: datetime) -> datetime:
    """Rows started before this are no longer read as in flight.

    The reaper's own age, so a press is never refused over a row the reaper
    would already have closed as abandoned.
    """
    return now - DEFAULT_REAP_AGE


def _step(step: str, umbrella: Mapping[str, Any], rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    """One step's state, from the rows of its kinds filed under the run."""
    if not rows:
        return {
            "step": step,
            "status": "pending" if umbrella["status"] == "running" else "not_run",
            "started_at": None,
            "finished_at": None,
            "rows": None,
            "error": None,
        }
    statuses = [r["status"] for r in rows]
    errors = [r["error"] for r in rows if r["status"] == "error"]
    seen = {r["kind"] for r in rows}
    incomplete = umbrella["status"] == "running" and not seen.issuperset(_STEP_KINDS[step])
    if errors:
        status = "error"
    elif "running" in statuses or incomplete:
        status = "running"
    elif all(s == "skipped" for s in statuses):
        status = "skipped"
    else:
        status = "ok"
    primary = next((r for r in rows if r["kind"] == _STEP_KINDS[step][0]), rows[0])
    finished = [r["finished_at"] for r in rows if r["finished_at"] is not None]
    return {
        "step": step,
        "status": status,
        "started_at": _iso(min(r["started_at"] for r in rows)),
        "finished_at": None if status == "running" or not finished else _iso(max(finished)),
        "rows": primary["rows"],
        "error": errors[0] if errors else None,
    }


def _shape_operator_run(umbrella: Mapping[str, Any], children: list[Mapping[str, Any]]) -> dict[str, Any]:
    # A run with no step rows at all -- recorded before steps were linked to
    # their run, or turned away before it wrote any -- is its own evidence:
    # its row says what happened to the step of its own kind.
    evidence = children or [umbrella]
    steps = [
        _step(step, umbrella, [r for r in evidence if r["kind"] in _STEP_KINDS[step]])
        for step in RUN_PLANS.get(umbrella["kind"], ())
    ]
    return {
        "run_id": umbrella["run_id"],
        "kind": umbrella["kind"],
        "status": umbrella["status"],
        "started_at": _iso(umbrella["started_at"]),
        "finished_at": _iso(umbrella["finished_at"]),
        "error": umbrella["error"],
        "steps": steps,
    }


def build_latest_runs(rows: Iterable[Mapping[str, Any]]) -> dict[int, dict[str, Any]]:
    """Each agency's newest operator run, read step by step.

    ``rows`` is :data:`LATEST_OPERATOR_RUNS_SQL`'s result: umbrella rows
    (``parent_run_id`` NULL) and the step rows filed under them.
    """
    rows = list(rows)
    children: dict[int, list[Mapping[str, Any]]] = {}
    for row in rows:
        if row["parent_run_id"] is not None:
            children.setdefault(row["parent_run_id"], []).append(row)
    newest: dict[int, Mapping[str, Any]] = {}
    for row in rows:
        if row["parent_run_id"] is not None:
            continue
        current = newest.get(row["agency_id"])
        if current is None or (row["started_at"], row["run_id"]) > (current["started_at"], current["run_id"]):
            newest[row["agency_id"]] = row
    return {
        agency_id: _shape_operator_run(umbrella, children.get(umbrella["run_id"], []))
        for agency_id, umbrella in newest.items()
    }
