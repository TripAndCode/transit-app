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

import hmac
import logging
import os

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from pipeline.locks import try_lock_ingest_analyze

router = APIRouter(prefix="/internal/cron", tags=["internal"], include_in_schema=False)

_log = logging.getLogger(__name__)


def _check_secret(request: Request) -> None:
    expected = os.environ.get("CRON_SECRET")
    if not expected:
        # Fail closed: refuse to run anything if the operator hasn't
        # configured a secret. A misconfigured deploy shouldn't expose
        # the ingest button.
        raise HTTPException(status_code=503, detail="CRON_SECRET not configured")
    if not hmac.compare_digest(request.headers.get("X-Cron-Secret") or "", expected):
        raise HTTPException(status_code=401, detail="Invalid cron secret")


def _run_ingest_and_analyze() -> None:
    """Pull live GTFS-RT for every agency, then refresh aggregations.

    Uses the existing sync CLI helpers via psycopg2 — keeps this module
    thin. Failures inside the loop are logged but don't abort the whole
    run, so one broken agency doesn't starve the others.
    """
    import psycopg2  # local import: keeps the import-graph cheap on cold starts

    from pipeline.analyze import analyze
    from pipeline.clickhouse import get_client
    from pipeline.freshness import check_agg_freshness
    from pipeline.ingest import ingest_live

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        _log.error("cron: DATABASE_URL not set; skipping ingest")
        return

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
        got_lock = try_lock_ingest_analyze(conn)
        conn.autocommit = False
        if not got_lock:
            _log.warning("cron: another ingest+analyze run is already in flight; skipping this poke")
            return
        with conn.cursor() as cur:
            cur.execute("SELECT agency_id FROM agencies WHERE deleted_at IS NULL ORDER BY agency_id")
            agency_ids = [r[0] for r in cur.fetchall()]
        if not agency_ids:
            _log.warning("cron: no agencies seeded; nothing to ingest")
            return

        for aid in agency_ids:
            try:
                ingest_live(aid, conn, ch_client)
            except Exception:
                _log.exception("cron: ingest_live failed for agency %s", aid)
            try:
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

    # No guard needed here: a lock miss, an empty roster, or a setup failure
    # each `return`/raise from inside the try above, which exits the whole
    # function once `finally` runs -- execution only ever reaches this line
    # when the lock was held and agencies were found.
    _run_weather_ingest(db_url)


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
