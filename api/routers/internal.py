"""Internal endpoints for scheduled cron jobs.

Designed to be called by an external scheduler (the repo ships a GitHub
Actions workflow that hits ``POST /internal/cron/ingest`` hourly), so we
don't need an always-on cron worker on Fly. Every endpoint is gated by
:envvar:`CRON_SECRET` passed via the ``X-Cron-Secret`` header — anything
without the matching header gets 401.

The actual ingest + analyze work runs as a FastAPI ``BackgroundTask`` so
the cron caller gets a fast 202 and doesn't block on the multi-minute
DB writes.
"""

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
    if request.headers.get("X-Cron-Secret") != expected:
        raise HTTPException(status_code=401, detail="Invalid cron secret")


def _run_ingest_and_analyze() -> None:
    """Pull live GTFS-RT for every agency, then refresh aggregations.

    Uses the existing sync CLI helpers via psycopg2 — keeps this module
    thin. Failures inside the loop are logged but don't abort the whole
    run, so one broken agency doesn't starve the others.
    """
    import psycopg2  # local import: keeps the import-graph cheap on cold starts

    from pipeline.analyze import analyze
    from pipeline.ingest import ingest_live

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        _log.error("cron: DATABASE_URL not set; skipping ingest")
        return

    conn = psycopg2.connect(db_url)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT agency_id FROM agencies ORDER BY agency_id")
            agency_ids = [r[0] for r in cur.fetchall()]
        if not agency_ids:
            _log.warning("cron: no agencies seeded; nothing to ingest")
            return

        for aid in agency_ids:
            try:
                ingest_live(aid, conn)
            except Exception:
                _log.exception("cron: ingest_live failed for agency %s", aid)
            try:
                analyze(aid, conn)
            except Exception:
                _log.exception("cron: analyze failed for agency %s", aid)
    finally:
        conn.close()


@router.post("/ingest", status_code=202)
async def cron_ingest(request: Request, background_tasks: BackgroundTasks) -> dict:
    """Kick off ingest_live + analyze for every agency in the background.

    Returns immediately with ``{"status": "started"}``. The actual work
    runs after the response is sent so the cron caller doesn't time out.
    """
    _check_secret(request)
    background_tasks.add_task(_run_ingest_and_analyze)
    return {"status": "started"}
