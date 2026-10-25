"""Internal endpoints for scheduled cron jobs.

``POST /internal/cron/ingest`` is the daily history path as well as a live
fallback. Per agency it fetches the feed into ``updates_live`` (``ingest_live``),
promotes every closed JST day from ``updates_live`` into ``updates``
(:mod:`pipeline.promote`), then runs ``analyze``. Nothing else promotes, so a
deployment must poke it at least once a day after JST midnight;
``updates_live``'s TTL drops a day that is never promoted. Every endpoint is
gated by :envvar:`CRON_SECRET` passed via the ``X-Cron-Secret`` header —
anything without the matching header gets 401.

The actual ingest + analyze work runs as a FastAPI ``BackgroundTask`` so
the cron caller gets a fast 202 and doesn't block on the multi-minute
DB writes.
"""

import asyncio
import hmac
import logging
import os
import pathlib
import re
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from typing import Any, NamedTuple

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from pipeline import runs as pipeline_runs
from pipeline.locks import try_lock_ingest_analyze_timed
from pipeline.ml_evaluation_ingest import ingest_evaluations
from pipeline.ml_forecast_ingest import ingest_forecast, s3_client_from_env, store_configured
from pipeline.url_guard import redact_urls_in_text

router = APIRouter(prefix="/internal/cron", tags=["internal"], include_in_schema=False)
collector_router = APIRouter(prefix="/internal/collector", tags=["internal"], include_in_schema=False)

_log = logging.getLogger(__name__)
_SOURCE_FILE_RE = re.compile(r"^[0-9]{8}/TripUpdate_[0-9]{6}\.pb$")
_MAX_COLLECTOR_PAYLOAD = 10 * 1024 * 1024


class UnknownAgency(ValueError):
    """A push for an agency that does not exist or is disabled."""


class SweepOutcome(NamedTuple):
    """What an operator's umbrella run row records about the sweep under it."""

    status: str
    error: str | None = None


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
                raise UnknownAgency(f"Unknown or deleted agency_id={agency_id}")
        # No advisory lock: a push writes updates_live, which analyze never
        # reads (pipeline/locks.py). ingest_live_payload's check-then-insert
        # over the last 10 minutes of captured_at absorbs a sequential
        # second arrival of the same file_name within that window; two
        # pushes that overlap in flight can both insert, and analyze's
        # argMax dedup on captured_at absorbs the duplicate from there.
        ch_client = get_client()
        return ingest_live_payload(agency_id, raw, captured_at, file_name, conn, ch_client)
    finally:
        # Nested try/finally: if conn.close() raises, ch_client.close() must
        # still run -- an unguarded `conn.close(); ch_client.close()` would
        # skip the ClickHouse close on a Postgres close error, leaking the
        # client + its HTTP pool inside this long-lived API process.
        try:
            if conn is not None:
                conn.close()
        finally:
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
    from pipeline.ingest import PayloadDecodeError, StaticTimetableMissing

    file_name = f"oracle/{source_file}"
    try:
        inserted = await asyncio.to_thread(
            _ingest_collector_payload,
            agency_id,
            raw,
            captured.astimezone(timezone.utc).isoformat(),
            file_name,
        )
    # 404 and 422 tell the collector that resending the same request cannot
    # help: its curl --retry resends on a 5xx, a 408 or a 429, and on neither
    # of these. A missing timetable is a 503 instead because the same bytes
    # ingest once an operator loads one. None of the three is this service
    # failing, so none logs a traceback.
    except UnknownAgency as exc:
        _log.warning("collector push for unknown or disabled agency %s", agency_id)
        raise HTTPException(status_code=404, detail="Unknown or disabled agency") from exc
    except PayloadDecodeError as exc:
        _log.warning("collector push for agency %s could not be decoded: %s", agency_id, exc)
        raise HTTPException(status_code=422, detail="Collector payload is not a decodable GTFS-RT feed") from exc
    except StaticTimetableMissing as exc:
        _log.warning("collector push refused: %s", exc)
        raise HTTPException(status_code=503, detail="Agency has no timetable loaded") from exc
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
    run_weather: bool = True,
    run_id: int | None = None,
    stop_on_failure: bool = False,
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
        outcome = _ingest_and_analyze_sweep(
            db_url,
            kind=kind,
            agency_ids=agency_ids,
            requested_by=requested_by,
            run_weather=run_weather,
            run_id=run_id,
            stop_on_failure=stop_on_failure,
        )
    except Exception as exc:
        _log.exception("cron: ingest+analyze run failed")
        # Redacted: a feed fetch's failure quotes the URL it was given, and
        # that URL routinely carries an API key. This row is read by every
        # operator with the board open.
        _finish_manual_run(db_url, run_id, "error", error=redact_urls_in_text(f"{type(exc).__name__}: {exc}"))
        return
    _finish_manual_run(db_url, run_id, outcome.status, error=outcome.error)


def _ingest_and_analyze_sweep(
    db_url: str,
    *,
    kind: str = "ingest",
    agency_ids: list[int] | None = None,
    requested_by: int | None = None,
    run_weather: bool = True,
    run_id: int | None = None,
    stop_on_failure: bool = False,
) -> SweepOutcome:
    """Pull live GTFS-RT for every agency, promote closed days, then refresh
    aggregations.

    Uses the existing sync CLI helpers via psycopg2 — keeps this module
    thin. Per agency, an ``ingest`` sweep runs three stages in order --
    ``ingest_live`` into `updates_live`, `pipeline.promote.promote_closed_days`
    out of it into `updates`, then `analyze` -- each recorded as its own
    `pipeline_runs` row. Failures inside the loop are logged but don't abort
    the whole run, so one broken agency or stage doesn't starve the others.
    Returns the outcome the umbrella row should record: ``skipped`` when the
    advisory lock turned this sweep away or no active agency was in scope,
    ``error`` naming every stage that failed, ``ok`` otherwise.

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

    ``run_weather=False`` is the operator-triggered path saying the fleet-wide
    weather fetch is not part of what was asked for; the scheduled poke leaves
    it at ``True``.

    ``run_id`` is the caller's own umbrella row, already open and closed by
    the caller. Every stage row is filed under it (``parent_run_id``), which
    is how the admin pages read the operator's run step by step, and this
    function knows not to open a second row for the same action when the
    advisory lock turns the sweep away.

    ``stop_on_failure`` ends an agency's stages at the first one that fails,
    for the agencies page's chained update, which promises exactly that. The
    default keeps going: analyzing what is already stored is still worth
    doing after a feed fetch failed.
    """
    import psycopg2  # local import: keeps the import-graph cheap on cold starts

    from pipeline.analyze import analyze
    from pipeline.clickhouse import get_client
    from pipeline.freshness import check_agg_freshness
    from pipeline.ingest import StaticTimetableMissing, ingest_live
    from pipeline.promote import promote_closed_days

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
    failed: list[str] = []
    ch_client = None
    conn = None
    try:
        ch_client = get_client()
        conn = psycopg2.connect(db_url)
        # Pin JST so any Postgres-side date cast agrees with the civil day the
        # read API serves under (api/main + gtfs_pipeline._get_conn both pin
        # JST); the cluster default is UTC. The aggregates are filed by service
        # date in ClickHouse (pipeline.service_day), not by this setting.
        # Committed up front (autocommit) so it survives analyze's txn rollback.
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
            if run_id is None:
                # The displaced sweep's only trace. Without it a deployment
                # whose pokes always collide looks identical to a healthy
                # one. Skipped when the caller already owns a row: that one
                # is closed as `skipped` by the caller, and a second would
                # draw two bars for one action.
                pipeline_runs.start_run(
                    conn, kind, status="skipped", lock_wait_ms=lock_wait_ms, requested_by=requested_by
                )
            return SweepOutcome("skipped")
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
            return SweepOutcome("skipped", "no active agency in scope")

        def _stage(stage: str, aid: int) -> AbstractContextManager[pipeline_runs.RunHandle]:
            return pipeline_runs.record_run(conn, stage, agency_id=aid, requested_by=requested_by, parent_run_id=run_id)

        for aid in agency_ids:
            stopped = False
            if kind == "ingest":
                try:
                    with _stage("ingest", aid) as run:
                        run.rows = ingest_live(aid, conn, ch_client)
                except StaticTimetableMissing as exc:
                    _log.warning("cron: %s", exc)
                    failed.append(f"ingest agency {aid} (no timetable loaded)")
                    stopped = stop_on_failure
                except Exception:
                    _log.exception("cron: ingest_live failed for agency %s", aid)
                    failed.append(f"ingest agency {aid}")
                    stopped = stop_on_failure
                if not stopped:
                    try:
                        with _stage("promote", aid) as run:
                            run.rows = promote_closed_days(aid, conn, ch_client)
                    except Exception:
                        _log.exception("cron: promotion failed for agency %s", aid)
                        failed.append(f"promote agency {aid}")
                        stopped = stop_on_failure
            if not stopped:
                try:
                    with _stage("analyze", aid):
                        analyze(aid, conn, ch_client)
                except Exception:
                    _log.exception("cron: analyze failed for agency %s", aid)
                    failed.append(f"analyze agency {aid}")

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
        # and ml_forecast passes below safe to run afterwards: neither ever
        # observes the lock as held, so neither can be blamed for one poke's
        # "already in flight" skip of another.

        # Nested try/finally: if conn.close() raises, ch_client.close() must
        # still run — an unguarded `conn.close(); ch_client.close()` would
        # skip the ClickHouse close on a Postgres close error, silently
        # leaking the client and its HTTP pool, which this block exists to prevent.
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
    # Skipped whenever the caller said so: a fleet-wide third-party fetch is
    # not what an operator asked for by pressing one button.
    if run_weather and kind == "ingest" and requested_agency_ids is None:
        _run_weather_ingest(db_url)
    # The published forecast is fleet-wide too (one R2 object, not one per
    # agency) -- same scoping as weather, but its own gate rather than
    # run_weather's: an operator who asked to skip the weather fetch hasn't
    # said anything about the ML forecast.
    if kind == "ingest" and requested_agency_ids is None:
        _run_ml_forecast_ingest(db_url)
    if failed:
        return SweepOutcome("error", "failed: " + ", ".join(failed))
    return SweepOutcome("ok")


def _refresh_static_step(
    db_url: str, agency_id: int, *, requested_by: int | None, parent_run_id: int | None
) -> pathlib.Path | None:
    """Reload one agency's timetable as a step of an operator's run: the zip
    it loaded, None when it loaded nothing newer, raising if the step failed.

    ``refresh_static_recorded`` is the command line's own reload and record,
    writing into the same directory, so the button and the command compare
    against the same change manifest and cannot drift apart. For an agency
    with no timetable loaded it fetches past that manifest, and a reload that
    still loads nothing for it fails, so a chain never goes on to ingest
    against an empty schedule.
    """
    import psycopg2

    from pipeline import static_fetcher

    conn = psycopg2.connect(db_url)
    try:
        return static_fetcher.refresh_static_recorded(
            agency_id,
            conn,
            pathlib.Path(static_fetcher.DEFAULT_STATIC_DEST),
            requested_by=requested_by,
            parent_run_id=parent_run_id,
        )
    finally:
        conn.close()


def _run_agency_update(
    *,
    agency_id: int,
    requested_by: int | None,
    run_id: int | None,
    then_ingest_and_analyze: bool,
) -> None:
    """The agencies page's timetable reload, optionally chained into the
    ingest + analyze sweep, closing the operator's umbrella row on every path.

    Steps run in order and a failed step stops the chain: a timetable that
    did not load leaves the umbrella ``error`` and the sweep never starts. A
    reload on its own closes its umbrella with its step's outcome, ``ok`` or
    ``unchanged``, so the board draws the two rows of one no-op check alike.
    The sweep is :func:`_run_ingest_and_analyze` itself, scoped to this one
    agency and asked to stop at its own first failure, so the lock, the JST
    pin and the freshness check are the cron job's own. It closes the
    umbrella from there; a sweep the advisory lock turns away leaves it
    ``skipped``, which the page reads as a stop, not a success.

    The timetable step runs outside the ingest/analyze lock, as the
    command-line reload does: holding that lock across a third-party
    download would turn every cron poke away for as long as the download
    takes. ``pipeline.locks`` covers why ``load_static`` is safe beside a
    running analyze.
    """
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        _log.error("admin: DATABASE_URL not set; skipping the timetable reload")
        return
    try:
        loaded = _refresh_static_step(db_url, agency_id, requested_by=requested_by, parent_run_id=run_id)
    except Exception as exc:
        _log.exception("admin: timetable reload failed for agency %s", agency_id)
        _finish_manual_run(db_url, run_id, "error", error=redact_urls_in_text(f"{type(exc).__name__}: {exc}"))
        return
    if not then_ingest_and_analyze:
        _finish_manual_run(db_url, run_id, "ok" if loaded is not None else "unchanged")
        return
    _run_ingest_and_analyze(
        kind="ingest",
        agency_ids=[agency_id],
        requested_by=requested_by,
        run_weather=False,
        run_id=run_id,
        stop_on_failure=True,
    )


def _ingest_ml_forecast(conn: Any) -> None:
    """Best-effort: the ML store's latest forecast and its weekly evaluation
    summaries, each step guarded on its own, so a missing/misconfigured
    bucket, a transient R2 failure, or one step failing never aborts the
    other or the surrounding ingest+analyze sweep. Both ingests already log
    and swallow their own read/write failures; these guards cover building
    the S3 client and anything else unexpected. An unconfigured store is
    skipped, not logged."""
    if not store_configured():
        return
    try:
        s3, bucket = s3_client_from_env()
    except Exception:
        _log.exception("cron: ML store client could not be built")
        return
    try:
        _log.info("cron: ml_forecast ingest upserted %d row(s)", ingest_forecast(conn, s3, bucket))
    except Exception:
        _log.exception("cron: ml_forecast ingest failed")
    try:
        _log.info("cron: ml_evaluation ingest upserted %d summary(ies)", ingest_evaluations(conn, s3, bucket))
    except Exception:
        _log.exception("cron: ml_evaluation ingest failed")


def _run_ml_forecast_ingest(db_url: str) -> None:
    """Reads the ML workbench's latest published forecast from R2 and
    upserts it into `agg_route_forecast_ml`, on its own connection opened
    only after `_run_ingest_and_analyze`'s advisory lock has already been
    released -- same reasoning as `_run_weather_ingest`'s own dedicated
    connection just below: a slow R2 round trip must not extend the
    "already in flight" window a concurrent poke would otherwise see.

    The env-var check happens before connecting, not just inside
    `_ingest_ml_forecast`: a deployment with no ML bucket configured should
    not pay for a throwaway connect/close on every sweep.
    """
    if not store_configured():
        return
    import psycopg2  # local import: keeps the import-graph cheap on cold starts

    try:
        ml_conn = psycopg2.connect(db_url)
    except Exception:
        _log.exception("cron: ml_forecast ingest failed to connect")
        return
    try:
        _ingest_ml_forecast(ml_conn)
    finally:
        ml_conn.close()


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
    """Kick off ingest_live + promote_closed_days + analyze for every agency
    in the background.

    Returns immediately with ``{"status": "started"}``. The actual work
    runs after the response is sent so the cron caller doesn't time out.
    """
    _check_secret(request)
    background_tasks.add_task(_run_ingest_and_analyze)
    return {"status": "started"}
