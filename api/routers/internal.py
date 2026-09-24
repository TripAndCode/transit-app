"""Internal endpoints for scheduled cron jobs.

This is the **fallback** ingest path. Production normally ingests the dense
Oracle archives via a daily Railway scheduled job (see
``docs/deploy-railway.md``); when object storage isn't wired, an external
scheduler can instead poke ``POST /internal/cron/ingest`` to run the
lower-fidelity ``ingest_live`` + ``analyze``. Every endpoint is gated by
:envvar:`CRON_SECRET` passed via the ``X-Cron-Secret`` header — anything
without the matching header gets 401.

The actual ingest + analyze work runs as a FastAPI ``BackgroundTask`` so
the cron caller gets a fast 202 and doesn't block on the multi-minute
DB writes.
"""

import asyncio
import hmac
import logging
import os
import re
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from pipeline import runs as pipeline_runs
from pipeline.locks import try_lock_ingest_analyze_timed

router = APIRouter(prefix="/internal/cron", tags=["internal"], include_in_schema=False)
collector_router = APIRouter(prefix="/internal/collector", tags=["internal"], include_in_schema=False)

_log = logging.getLogger(__name__)
_SOURCE_FILE_RE = re.compile(r"^[0-9]{8}/TripUpdate_[0-9]{6}\.pb$")
_MAX_COLLECTOR_PAYLOAD = 10 * 1024 * 1024


def _check_secret(request: Request) -> None:
    expected = os.environ.get("CRON_SECRET")
    if not expected:
        # Fail closed: refuse to run anything if the operator hasn't
        # configured a secret. A misconfigured deploy shouldn't expose
        # the ingest button.
        raise HTTPException(status_code=503, detail="CRON_SECRET not configured")
    if not hmac.compare_digest(request.headers.get("X-Cron-Secret") or "", expected):
        raise HTTPException(status_code=401, detail="Invalid cron secret")


def _check_collector_secret(request: Request) -> None:
    expected = os.environ.get("COLLECTOR_INGEST_SECRET")
    if not expected:
        raise HTTPException(status_code=503, detail="COLLECTOR_INGEST_SECRET not configured")
    if not hmac.compare_digest(request.headers.get("X-Collector-Secret") or "", expected):
        raise HTTPException(status_code=401, detail="Invalid collector secret")


def _ingest_collector_payload(agency_id: int, raw: bytes, captured_at: str, file_name: str) -> int:
    import psycopg2

    from pipeline.clickhouse import get_client
    from pipeline.ingest import ingest_live_payload
    from pipeline.locks import try_lock_ingest_analyze

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL is not configured")
    conn = None
    ch_client = None
    try:
        conn = psycopg2.connect(db_url)
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("SET TIME ZONE 'Asia/Tokyo'")
            cur.execute(
                "SELECT 1 FROM agencies WHERE agency_id = %s AND deleted_at IS NULL",
                (agency_id,),
            )
            if cur.fetchone() is None:
                raise ValueError(f"Unknown or deleted agency_id={agency_id}")
        if not try_lock_ingest_analyze(conn):
            raise HTTPException(status_code=409, detail="A data ingest is already in progress")
        conn.autocommit = False
        ch_client = get_client()
        return ingest_live_payload(agency_id, raw, captured_at, file_name, conn, ch_client)
    finally:
        if conn is not None:
            conn.close()
        if ch_client is not None:
            ch_client.close()


@collector_router.post("/updates/{agency_id}")
async def collector_update(agency_id: int, request: Request) -> dict:
    """Receive one protobuf poll from the Oracle collector.

    The collector keeps polling all agencies and retries delivery; the
    durable source filename makes retries idempotent. Work is synchronous from
    the collector's perspective so a 2xx means ClickHouse accepted the data.
    """
    _check_collector_secret(request)
    source_file = request.headers.get("X-Source-File") or ""
    if not _SOURCE_FILE_RE.fullmatch(source_file):
        raise HTTPException(status_code=400, detail="Invalid X-Source-File")
    captured_header = request.headers.get("X-Captured-At") or ""
    try:
        captured = datetime.fromisoformat(captured_header.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid X-Captured-At") from exc
    if captured.tzinfo is None:
        raise HTTPException(status_code=400, detail="X-Captured-At must include timezone")
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared_length = int(content_length)
        except ValueError:
            declared_length = None
        if declared_length is not None and declared_length > _MAX_COLLECTOR_PAYLOAD:
            raise HTTPException(status_code=413, detail="Collector payload is empty or too large")
    chunks = bytearray()
    async for chunk in request.stream():
        chunks.extend(chunk)
        if len(chunks) > _MAX_COLLECTOR_PAYLOAD:
            raise HTTPException(status_code=413, detail="Collector payload is empty or too large")
    raw = bytes(chunks)
    if not raw:
        raise HTTPException(status_code=413, detail="Collector payload is empty or too large")
    file_name = f"oracle/{source_file}"
    try:
        inserted = await asyncio.to_thread(
            _ingest_collector_payload,
            agency_id,
            raw,
            captured.astimezone(timezone.utc).isoformat(),
            file_name,
        )
    except HTTPException:
        raise
    except Exception as exc:
        _log.exception("collector ingest failed for agency %s", agency_id)
        raise HTTPException(status_code=502, detail="Collector payload could not be ingested") from exc
    return {"status": "accepted", "inserted": inserted}


def _finish_manual_run(db_url: str, run_id: int | None, status: str, error: str | None = None) -> None:
    """Close an operator-triggered run row on a connection of its own.

    Deliberately not the sweep's own connection: that one may be absent (the
    sweep failed during setup) or mid-failure, and the row an operator is
    watching has to be closed on every path, including the ones where the
    work connection never existed. One short connection per manual trigger is
    a cheap price for a bar that always stops moving.
    """
    if run_id is None:
        return
    import psycopg2

    try:
        conn = psycopg2.connect(db_url)
    except Exception:
        _log.exception("cron: could not connect to close manual run %s", run_id)
        return
    try:
        pipeline_runs.finish_run(conn, run_id, status, error=error)
    finally:
        conn.close()


def _run_ingest_and_analyze(
    *,
    kind: str = "ingest",
    agency_ids: list[int] | None = None,
    requested_by: int | None = None,
    run_id: int | None = None,
) -> None:
    """Run the sweep and close the operator's umbrella run row after it.

    The keyword arguments are the operator-triggered path (`POST
    /api/admin/runs`); the cron poke calls this with none of them and behaves
    exactly as before. ``run_id`` is the row the operator is watching — it is
    closed on every path, including the ones where the sweep's own connection
    never existed, so a triggered bar always stops moving.
    """
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        # No row to close either: the admin route that opens one needs the
        # same database, so it cannot have answered on a deployment where
        # this is unset.
        _log.error("cron: DATABASE_URL not set; skipping ingest")
        return
    try:
        status = _ingest_and_analyze_sweep(db_url, kind=kind, agency_ids=agency_ids, requested_by=requested_by)
    except Exception as exc:
        _log.exception("cron: ingest+analyze run failed")
        _finish_manual_run(db_url, run_id, "error", error=f"{type(exc).__name__}: {exc}")
        return
    _finish_manual_run(db_url, run_id, status)


def _ingest_and_analyze_sweep(
    db_url: str,
    *,
    kind: str = "ingest",
    agency_ids: list[int] | None = None,
    requested_by: int | None = None,
) -> str:
    """Pull live GTFS-RT for every agency, then refresh aggregations.

    Uses the existing sync CLI helpers via psycopg2 — keeps this module
    thin. Failures inside the loop are logged but don't abort the whole
    run, so one broken agency doesn't starve the others. Returns the outcome
    the umbrella row should record: ``skipped`` when the advisory lock turned
    this sweep away, ``ok`` otherwise.

    ``kind="analyze"`` re-aggregates what is already stored without fetching —
    the one case where skipping the feed pull and the weather pass is what was
    asked for.

    ``agency_ids`` narrows the run, which is what the admin drawer's
    per-agency re-analyze action uses: the same lock, the same JST session
    pin, and the same post-run freshness check as a scheduled poke, rather
    than a second implementation of them. A scoped run skips the weather
    pass — observed weather is per station, not per agency, so one agency's
    re-analyze has no reason to re-drive a third-party fetch for the whole
    fleet. Scoped or not, the roster is filtered through `deleted_at IS
    NULL`: a disabled agency is not work this is allowed to do.
    """
    import psycopg2  # local import: keeps the import-graph cheap on cold starts

    from pipeline.analyze import analyze
    from pipeline.clickhouse import get_client
    from pipeline.freshness import check_agg_freshness
    from pipeline.ingest import ingest_live

    # Kept apart from the resolved roster below: the caller's scope decides
    # whether the weather pass runs, and the roster is overwritten with the
    # agencies that actually exist.
    requested_agency_ids = agency_ids

    # Both acquired inside the try below (not here) so that a failure
    # anywhere in setup -- get_client(), psycopg2.connect(), SET TIME ZONE,
    # or the lock acquisition itself -- still reaches the finally block and
    # closes whichever of the two was actually created, instead of leaking
    # a ClickHouse client + HTTP pool (or a Postgres session) per failed
    # poke inside this long-lived API process.
    ch_client = None
    conn = None
    try:
        ch_client = get_client()
        conn = psycopg2.connect(db_url)
        # Pin JST so analyze() buckets `captured_at::date` on the same civil
        # day the read API serves under (api/main + gtfs_pipeline._get_conn
        # both pin JST); the cluster default is UTC, which would mis-bucket
        # ~20% of rows by date and also desync agg_route_daily from the
        # JST-based freshness check below. Committed up front (autocommit)
        # so it survives analyze's txn rollback.
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("SET TIME ZONE 'Asia/Tokyo'")
        # BackgroundTasks runs on a thread pool, so two rapid POSTs (a double
        # cron poke, or a retry while the first request already completed
        # server-side) schedule two independently-running tasks; this also
        # guards a cron poke overlapping a scheduled gtfs_pipeline.py CLI
        # run, since both take the same lock (pipeline/locks.py). Non-blocking
        # `False` rather than queuing -- BackgroundTasks has no caller to
        # report failure to, so skipping is the right behavior here.
        got_lock, lock_wait_ms = try_lock_ingest_analyze_timed(conn)
        conn.autocommit = False
        if not got_lock:
            _log.warning("cron: another ingest+analyze run is already in flight; skipping this poke")
            # The displaced sweep's only trace. Without it a deployment whose
            # pokes always collide looks identical to a healthy one.
            pipeline_runs.start_run(conn, kind, status="skipped", lock_wait_ms=lock_wait_ms, requested_by=requested_by)
            return "skipped"
        with conn.cursor() as cur:
            if requested_agency_ids is None:
                cur.execute("SELECT agency_id FROM agencies WHERE deleted_at IS NULL ORDER BY agency_id")
            else:
                cur.execute(
                    "SELECT agency_id FROM agencies "
                    "WHERE deleted_at IS NULL AND agency_id = ANY(%s) ORDER BY agency_id",
                    (requested_agency_ids,),
                )
            agency_ids = [r[0] for r in cur.fetchall()]
        if not agency_ids:
            _log.warning("cron: no agencies to ingest (scope=%s)", requested_agency_ids or "all")
            return "ok"

        for aid in agency_ids:
            if kind == "ingest":
                try:
                    with pipeline_runs.record_run(conn, "ingest", agency_id=aid, requested_by=requested_by) as run:
                        run.rows = ingest_live(aid, conn, ch_client)
                except Exception:
                    _log.exception("cron: ingest_live failed for agency %s", aid)
            try:
                with pipeline_runs.record_run(conn, "analyze", agency_id=aid, requested_by=requested_by):
                    analyze(aid, conn, ch_client)
            except Exception:
                _log.exception("cron: analyze failed for agency %s", aid)

        # Catch the mid-loop-crash hole: if any agency's aggs lag its newest
        # completed day, surface it loudly. Read-only; never aborts the run.
        # Isolated in its own try/except, like the per-agency loop above: a
        # failure here (e.g. a transient ClickHouse error) must not also
        # suppress the weather pass below, which has nothing to do with
        # aggregate freshness.
        try:
            stale = check_agg_freshness(conn, ch_client, agency_ids)
            if stale:
                _log.error(
                    "cron: %d agency(ies) have stale aggregates after analyze: %s",
                    len(stale),
                    [s.agency_id for s in stale],
                )
            else:
                _log.info("cron: all %d agencies have fresh aggregates", len(agency_ids))
        except Exception:
            _log.exception("cron: check_agg_freshness failed")
    finally:
        # No explicit pg_advisory_unlock call: conn.close() below ends the
        # session, and Postgres releases every session-level advisory lock
        # a session holds when it ends. This is also what makes the weather
        # pass below safe to run afterwards: it never observes the lock as
        # held, so it can never be blamed for one poke's "already in flight"
        # skip of another.
        #
        # Nested try/finally: if conn.close() raises, ch_client.close() must
        # still run — an unguarded `conn.close(); ch_client.close()` would
        # skip the ClickHouse close on a Postgres close error, silently
        # reintroducing the client + HTTP pool leak this block exists to fix.
        # Both guarded with `is not None`: get_client()/psycopg2.connect()
        # itself can be what raised, leaving the other -- or both -- unset.
        try:
            if conn is not None:
                conn.close()
        finally:
            # Close the sync ClickHouse client's underlying HTTP connection
            # pool. This job runs as a BackgroundTask inside the long-lived
            # API process, so a missing close() here leaks one client + pool
            # per invocation of this endpoint instead of one per short-lived
            # CLI run.
            if ch_client is not None:
                ch_client.close()

    # No guard needed for the lock/roster/setup cases: each of those
    # `return`s or raises from inside the try above, which exits the whole
    # function once `finally` runs -- execution only ever reaches this line
    # when the lock was held and agencies were found.
    #
    # Skipped for an analyze-only run: that asks for the stored data to be
    # re-aggregated, and the weather pass is a fetch from a third party with
    # nothing to do with aggregation. Skipped for a scoped run for the same
    # reason in reverse: observed weather is per station, not per agency.
    if kind == "ingest" and requested_agency_ids is None:
        _run_weather_ingest(db_url)
    return "ok"


def _run_weather_ingest(db_url: str) -> None:
    """Observed daily weather for each agency's representative station.

    Runs on its OWN connection, opened only after `_run_ingest_and_analyze`
    has already closed the connection that held the ingest+analyze advisory
    lock -- deliberately, not incidentally. A trickling third-party response
    can hold a single socket read open past any per-operation timeout (see
    `pipeline.weather.CRON_INGEST_BUDGET_SEC`), and this call is the only
    work in the cron job that waits on a third party outside the agencies'
    own feeds. Sharing the lock-holding connection would let that overrun
    keep the advisory lock taken, and every cron poke arriving meanwhile
    would take the "already in flight" path -- dropping live GTFS-RT polls,
    which are unrecoverable once their moment has passed. A dedicated
    connection makes that impossible by construction: this pass cannot hold
    a lock it never took, no matter how long a slow source keeps it open.
    Safe because `ingest_weather` needs nothing from the ingest+analyze
    transaction -- it already commits per station-day on whatever
    connection it is given.

    `ingest_weather` checks its own kill switch and skips every fetch when
    it is off, so no flag test belongs here (a check here could diverge
    from the CLI's). A failure here is logged and never allowed to affect
    the ingest+analyze work above, which has already fully committed by the
    time this runs.

    No longer serialized against a concurrent poke's own weather pass (the
    advisory lock above no longer covers this call at all): two pokes close
    enough together can each decide the same station-day needs fetching and
    both fetch it. Left unguarded because `ingest_weather`'s upsert makes a
    duplicate fetch merely wasteful, never incorrect.
    """
    import psycopg2  # local import: keeps the import-graph cheap on cold starts

    from pipeline.weather import CRON_INGEST_BUDGET_SEC, ingest_weather

    try:
        weather_conn = psycopg2.connect(db_url)
    except Exception:
        _log.exception("cron: weather ingest failed to connect")
        return
    try:
        ingest_weather(weather_conn, max_seconds=CRON_INGEST_BUDGET_SEC)
    except Exception:
        _log.exception("cron: weather ingest failed")
    finally:
        weather_conn.close()


@router.post("/ingest", status_code=202)
async def cron_ingest(request: Request, background_tasks: BackgroundTasks) -> dict:
    """Kick off ingest_live + analyze for every agency in the background.

    Returns immediately with ``{"status": "started"}``. The actual work
    runs after the response is sent so the cron caller doesn't time out.
    """
    _check_secret(request)
    background_tasks.add_task(_run_ingest_and_analyze)
    return {"status": "started"}
