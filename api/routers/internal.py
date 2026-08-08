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
    conn.autocommit = False
    try:
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
        conn.close()
        # Close the sync ClickHouse client's underlying HTTP connection pool.
        # This job runs as a BackgroundTask inside the long-lived API
        # process, so a missing close() here leaks one client + pool per
        # invocation of this endpoint instead of one per short-lived CLI run.
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
