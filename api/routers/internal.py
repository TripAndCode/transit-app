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

router = APIRouter(prefix="/internal/cron", tags=["internal"], include_in_schema=False)

_log = logging.getLogger(__name__)

# Postgres advisory lock key for _run_ingest_and_analyze. BackgroundTasks runs
# on a thread pool, so two rapid POST /internal/cron/ingest calls (a double
# cron poke, or a caller retrying on a slow response while the first request
# still completed server-side) schedule two independently-running tasks. Both
# would otherwise: (a) run analyze() concurrently on separate psycopg2
# connections -- NOT safe: each does DELETE FROM agg_* WHERE agency_id=...
# then re-INSERTs the same PKs in its own transaction, so the loser hits a
# unique-violation once the winner commits, not a harmless no-op; and
# (b) both call ingest_live for every agency, and ClickHouse has no ON
# CONFLICT DO NOTHING to absorb the resulting duplicate poll (see
# pipeline/clickhouse.py's insert_updates docstring) beyond the single-file
# bounded check recent_file_name_exists already does. An arbitrary fixed
# key, chosen once — only needs to be distinct from any other advisory lock
# this codebase takes, and there are none as of this writing.
#
# Scope note: this lock only covers THIS endpoint. gtfs_pipeline.py's CLI
# commands (cmd_ingest_live, cmd_analyze_all) -- the primary production
# ingest path per this module's docstring above -- take no equivalent lock,
# so a cron poke overlapping a scheduled CLI run is still unprotected.
# Extending the lock there is a reasonable follow-up, not done here to keep
# this fix scoped to the endpoint that motivated it.
_CRON_LOCK_KEY = 72710001


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

    ch_client = get_client()
    conn = psycopg2.connect(db_url)
    # Pin JST so analyze() buckets `captured_at::date` on the same civil day the
    # read API serves under (api/main + gtfs_pipeline._get_conn both pin JST);
    # the cluster default is UTC, which would mis-bucket ~20% of rows by date
    # and also desync agg_route_daily from the JST-based freshness check below.
    # Committed up front (autocommit) so it survives analyze's txn rollback.
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("SET TIME ZONE 'Asia/Tokyo'")
        # Session-scoped advisory lock (not txn-scoped: this connection stays
        # autocommit=False for the ingest/analyze work below, and the lock
        # must hold across every one of those transactions, not just one).
        # A concurrently-running invocation of this same job gets `False`
        # back immediately rather than blocking -- BackgroundTasks has no
        # caller to report failure to, so skipping is the right behavior,
        # not queuing.
        cur.execute("SELECT pg_try_advisory_lock(%s)", (_CRON_LOCK_KEY,))
        got_lock = cur.fetchone()[0]
    conn.autocommit = False
    try:
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
        stale = check_agg_freshness(conn, ch_client, agency_ids)
        if stale:
            _log.error(
                "cron: %d agency(ies) have stale aggregates after analyze: %s",
                len(stale),
                [s.agency_id for s in stale],
            )
        else:
            _log.info("cron: all %d agencies have fresh aggregates", len(agency_ids))
    finally:
        # No explicit pg_advisory_unlock call: conn.close() below ends the
        # session, and Postgres releases every session-level advisory lock
        # a session holds when it ends.
        #
        # Nested try/finally: if conn.close() raises, ch_client.close() must
        # still run — an unguarded `conn.close(); ch_client.close()` would
        # skip the ClickHouse close on a Postgres close error, silently
        # reintroducing the client + HTTP pool leak this block exists to fix.
        try:
            conn.close()
        finally:
            # Close the sync ClickHouse client's underlying HTTP connection
            # pool. This job runs as a BackgroundTask inside the long-lived
            # API process, so a missing close() here leaks one client + pool
            # per invocation of this endpoint instead of one per short-lived
            # CLI run.
            ch_client.close()


@router.post("/ingest", status_code=202)
async def cron_ingest(request: Request, background_tasks: BackgroundTasks) -> dict:
    """Kick off ingest_live + analyze for every agency in the background.

    Returns immediately with ``{"status": "started"}``. The actual work
    runs after the response is sent so the cron caller doesn't time out.
    """
    _check_secret(request)
    background_tasks.add_task(_run_ingest_and_analyze)
    return {"status": "started"}
