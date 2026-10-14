"""Per-agency static GTFS refresh orchestrator.

Resolves the agency's static_strategy + static_url from the DB, dispatches to
the strategy, and on a fresh zip calls pipeline.static_loader.load_static.
"""

import logging
import pathlib
from typing import Optional

from pipeline import runs as pipeline_runs
from pipeline.static_loader import load_static
from pipeline.strategies import get_static_strategy
from pipeline.strategies.errors import StaticFetchError

logger = logging.getLogger(__name__)

#: Where fetched zips and each strategy's change manifest live, relative to
#: the working directory. The command line's default and the admin page's
#: reload share it, so both compare against the same manifest.
DEFAULT_STATIC_DEST = "raw_archives_static"


def refresh_static(agency_id: int, conn, dest_dir: pathlib.Path, *, force: bool = False) -> Optional[pathlib.Path]:
    """Refresh static GTFS for one agency. Returns the loaded zip path, or
    None when nothing is newer; the strategy's StaticFetchError propagates.

    "Nothing newer" is measured against the strategy's change manifest on
    disk, which can outlive the timetable tables (a restored or rebuilt
    database, an agency's static rows cleared). The fetch is forced, setting
    the manifest aside, when ``force`` asks for it and whenever the agency has
    no timetable loaded, so a stale manifest cannot keep an agency without
    one. Forcing is also how a column the loader derives reaches a timetable
    whose source has not changed.
    """
    from pipeline.db import _static_loaded

    with conn.cursor() as cur:
        cur.execute(
            "SELECT static_url, static_strategy FROM agencies WHERE agency_id = %s",
            (agency_id,),
        )
        row = cur.fetchone()
    if row is None:
        logger.warning(f"[static_fetcher] no agency {agency_id}")
        return None
    static_url, strategy_name = row
    if not static_url or not strategy_name:
        logger.warning(f"[static_fetcher] agency={agency_id} not configured for static refresh")
        return None

    strategy = get_static_strategy(strategy_name)
    if not force and not _static_loaded(conn, agency_id):
        logger.info(f"[static_fetcher] agency={agency_id} has no timetable loaded; fetching past the manifest")
        force = True
    zip_path = strategy.fetch(agency_id, static_url, dest_dir, force=force)
    if zip_path is None:
        return None

    load_static(str(zip_path), agency_id, conn)
    # load_static already committed, so the load itself succeeded; losing the
    # dedup marker only costs one harmless extra reload next run, which is
    # strictly better than this call raising and reporting a genuinely
    # successful load as a failure (and rolling back a connection that has
    # nothing left to roll back).
    try:
        strategy.record_loaded(agency_id, dest_dir, zip_path)
    except Exception:
        logger.warning(f"[static_fetcher] agency={agency_id} loaded but could not record it", exc_info=True)
    logger.info(f"[static_fetcher] agency={agency_id} loaded {zip_path.name}")
    return zip_path


def refresh_static_recorded(
    agency_id: int,
    conn,
    dest_dir: pathlib.Path,
    *,
    requested_by: int | None = None,
    parent_run_id: int | None = None,
    force: bool = False,
) -> Optional[pathlib.Path]:
    """:func:`refresh_static`, recorded as a ``static`` run.

    The row closes ``ok`` with the loaded timetable's route count when a zip
    loaded, ``unchanged`` when the source had nothing newer, and ``error``
    when the reload raised, so "last loaded" (a successful ``static`` run)
    means a load. A fetch that failed raises :class:`StaticFetchError`, whose
    reason becomes the row's error.
    """
    with pipeline_runs.record_run(
        conn, "static", agency_id=agency_id, requested_by=requested_by, parent_run_id=parent_run_id
    ) as run:
        loaded = refresh_static(agency_id, conn, dest_dir, force=force)
        if loaded is None:
            run.status = "unchanged"
        else:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM static_routes WHERE agency_id = %s", (agency_id,))
                run.rows = cur.fetchone()[0]
    return loaded


def refresh_all(conn, dest_dir: pathlib.Path, *, force: bool = False) -> tuple[int, int, list[int]]:
    """Refresh static for every agency that has both static_url and static_strategy,
    forcing each fetch when ``force`` is set (see :func:`refresh_static`).

    Run-all-then-report, matching cmd_analyze_all/cmd_ingest_live: one
    agency's failure (a source that could not be fetched, a corrupt zip)
    does not abort the rest, and every failure is collected and returned
    (alongside the attempted total) so the caller can exit nonzero and log
    an "N of M failed" line instead of reporting a partial run as a full
    success. refresh_static()/load_static() have no internal
    rollback-on-error of their own, so the connection is rolled back here
    before continuing to the next agency.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT agency_id FROM agencies "
            "WHERE static_url IS NOT NULL AND static_strategy IS NOT NULL "
            "AND deleted_at IS NULL "
            "ORDER BY agency_id"
        )
        ids = [r[0] for r in cur.fetchall()]
    n_loaded = 0
    failed = []
    for aid in ids:
        try:
            if refresh_static_recorded(aid, conn, dest_dir, force=force):
                n_loaded += 1
        except StaticFetchError as e:
            logger.error(f"[static_fetcher] refresh failed for agency {aid}: {e}")
            conn.rollback()
            failed.append(aid)
        except Exception:
            logger.exception(f"[static_fetcher] refresh failed for agency {aid}")
            conn.rollback()
            failed.append(aid)
    return n_loaded, len(ids), failed
