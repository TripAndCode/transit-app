"""One row per pipeline job, so the control board can show what actually ran.

``pipeline_runs`` records ingest, analyze, weather and static-load jobs from
both entry points -- the CLI (``gtfs_pipeline.py``) and the cron fallback
endpoint -- including the jobs that never did any work because another
process held the ingest/analyze advisory lock. A run that was skipped is the
most interesting row on the board: it is the only evidence that a scheduled
job was displaced, and nothing else in the system keeps it.

Two properties the helpers here exist to guarantee:

**Bookkeeping never breaks the job.** Every statement is wrapped: a schema
that predates this table, a revoked grant, or a connection that has gone away
costs the run its row and nothing else. The pipeline's output is the product;
the timeline is commentary on it.

**The row outlives the work's own transaction.** ``analyze()`` rolls its
transaction back before re-raising, and ``ingest()`` can leave a failed one
behind, so a row written inside the work's transaction would be erased
exactly in the failure case the board most needs to show. Each write here
therefore commits by itself, and the failure path rolls the work's aborted
transaction back first so the error row is writable at all.

That commit is also why the connection passed in must be at a point where
committing is safe -- i.e. before the work starts or after it has finished
and already committed or rolled back its own transaction. Every call site
sits on such a boundary.
"""

from __future__ import annotations

import logging
import traceback
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterator

from pipeline.url_guard import redact_urls_in_text

logger = logging.getLogger(__name__)

#: Mirrors the ``kind`` CHECK constraint. Validated before the statement is
#: built so a typo fails here, where the caller's stack still says which job
#: it was, rather than as a constraint violation swallowed by the guard below.
RUN_KINDS: tuple[str, ...] = ("ingest", "analyze", "weather", "static")

#: Mirrors the ``status`` CHECK constraint.
RUN_STATUSES: tuple[str, ...] = ("running", "ok", "skipped", "error")

#: A stored error is a human-readable marker on a timeline bar, not a log
#: replacement -- the traceback itself goes to the logger. Bounded so one
#: pathological exception cannot grow a row without limit.
MAX_ERROR_CHARS = 2000

_INSERT_RUN_SQL = """
    INSERT INTO pipeline_runs (kind, agency_id, started_at, status, lock_wait_ms, requested_by)
    VALUES (%s, %s, now(), %s, %s, %s)
    RETURNING run_id
"""

_FINISH_RUN_SQL = """
    UPDATE pipeline_runs
    SET status = %s, finished_at = now(), rows = %s, error = %s
    WHERE run_id = %s
"""

#: Closes rows whose process died before it could. ``finished_at`` is the
#: reap moment rather than the cutoff: the row records when the run was given
#: up on, and the cutoff only decides which rows qualify.
_REAP_ABANDONED_SQL = """
    UPDATE pipeline_runs
    SET status = 'error', error = 'abandoned', finished_at = %s
    WHERE status = 'running' AND finished_at IS NULL AND started_at < %s
"""

#: How long a ``running`` row is left alone before it is treated as
#: abandoned. Comfortably longer than the slowest real sweep, since reaping a
#: run that is still working would replace a true bar with a false error.
DEFAULT_REAP_AGE = timedelta(hours=2)


@dataclass
class RunHandle:
    """The mutable half of :func:`record_run`: the job's reported row count.

    Set ``rows`` inside the block when the job returns one; left ``None`` the
    column stays NULL, which the board reads as "this job kind has no row
    count", not as zero rows.
    """

    run_id: int | None
    rows: int | None = None


def _rollback(conn) -> None:
    """Clear an aborted transaction so the next statement can run at all.

    Never raises: this is called on paths that are already handling a
    failure, and a connection too broken to roll back is also too broken to
    record anything.
    """
    try:
        conn.rollback()
    except Exception:
        logger.debug("pipeline_runs: rollback failed", exc_info=True)


def start_run(
    conn,
    kind: str,
    agency_id: int | None = None,
    requested_by: int | None = None,
    lock_wait_ms: int | None = None,
    status: str = "running",
) -> int | None:
    """Open a run row and return its id, or ``None`` if it could not be written.

    ``status`` is ``running`` for a job about to start; a job that never
    started -- one displaced by the advisory lock -- is recorded whole here
    instead, as ``skipped``, since it has no later moment to be finished at.
    """
    if kind not in RUN_KINDS:
        raise ValueError(f"unknown run kind {kind!r}")
    if status not in RUN_STATUSES:
        raise ValueError(f"unknown run status {status!r}")
    try:
        with conn.cursor() as cur:
            cur.execute(_INSERT_RUN_SQL, (kind, agency_id, status, lock_wait_ms, requested_by))
            row = cur.fetchone()
        conn.commit()
        return row[0] if row else None
    except Exception:
        logger.warning("pipeline_runs: could not record the start of a %s run", kind, exc_info=True)
        _rollback(conn)
        return None


def finish_run(
    conn,
    run_id: int | None,
    status: str,
    rows: int | None = None,
    error: str | None = None,
) -> None:
    """Close a run row. A ``run_id`` of ``None`` -- an unrecordable start --
    is a no-op, so call sites need no second guard of their own."""
    if run_id is None:
        return
    if status not in RUN_STATUSES:
        raise ValueError(f"unknown run status {status!r}")
    try:
        with conn.cursor() as cur:
            cur.execute(_FINISH_RUN_SQL, (status, rows, error[:MAX_ERROR_CHARS] if error else None, run_id))
        conn.commit()
    except Exception:
        logger.warning("pipeline_runs: could not record the end of run %s", run_id, exc_info=True)
        _rollback(conn)


@contextmanager
def record_run(
    conn,
    kind: str,
    agency_id: int | None = None,
    requested_by: int | None = None,
) -> Iterator[RunHandle]:
    """Bracket one job with its run row: ``running`` -> ``ok`` or ``error``.

    The exception is always re-raised -- every caller's own failure handling
    (the per-agency loops' log-and-continue, the single-agency commands'
    propagate-and-exit) stays exactly as it was.
    """
    handle = RunHandle(run_id=start_run(conn, kind, agency_id=agency_id, requested_by=requested_by))
    try:
        yield handle
    except BaseException as exc:
        # The work may have left its transaction aborted; the error row is
        # only writable once that is cleared.
        _rollback(conn)
        finish_run(conn, handle.run_id, "error", rows=handle.rows, error=_describe(exc))
        raise
    finish_run(conn, handle.run_id, "ok", rows=handle.rows)


def reap_abandoned_runs(conn, *, older_than: timedelta = DEFAULT_REAP_AGE, now: datetime | None = None) -> int:
    """Close rows no process is left to close, and return how many.

    A worker killed mid-job (SIGKILL, OOM, a redeploy) never reaches
    :func:`finish_run`, so its row stays ``running`` with a NULL
    ``finished_at`` forever. The board draws that as a bar with no end, which
    is indistinguishable from work genuinely still in flight -- the one thing
    an operator reads this timeline to tell apart.

    Like every other write here, a failure costs the sweep and nothing else:
    an environment whose schema predates the table reports zero reaped.
    """
    now = now or datetime.now(timezone.utc)
    try:
        with conn.cursor() as cur:
            cur.execute(_REAP_ABANDONED_SQL, (now, now - older_than))
            reaped = cur.rowcount
        conn.commit()
        if reaped:
            logger.warning("pipeline_runs: closed %d run(s) left open by a process that died", reaped)
        return reaped or 0
    except Exception:
        logger.warning("pipeline_runs: could not reap abandoned runs", exc_info=True)
        _rollback(conn)
        return 0


def reap_abandoned_runs_best_effort(db_url: str | None, *, older_than: timedelta = DEFAULT_REAP_AGE) -> int:
    """:func:`reap_abandoned_runs` on a connection of its own, never raising.

    The callers that want this -- API startup and the control board -- hold
    an asyncpg connection, not a psycopg2 one, and run it off the request
    path. A short dedicated connection keeps the statement in one place
    instead of growing a second dialect of it.
    """
    if not db_url:
        logger.warning("pipeline_runs: no database URL; skipping the abandoned-run sweep")
        return 0
    import psycopg2

    try:
        conn = psycopg2.connect(db_url)
    except Exception:
        logger.warning("pipeline_runs: could not connect to reap abandoned runs", exc_info=True)
        return 0
    try:
        return reap_abandoned_runs(conn, older_than=older_than)
    finally:
        conn.close()


def _describe(exc: BaseException) -> str:
    """The exception's type and message -- the one line a timeline bar can
    show. The traceback stays with the caller's own logging.

    Redacted first: a feed fetch's failure quotes the URL it was given, and
    that URL routinely carries an API key. The row is read by every operator
    with the board open and outlives the job, so the credential must not
    reach it.
    """
    return redact_urls_in_text("".join(traceback.format_exception_only(type(exc), exc)).strip())
