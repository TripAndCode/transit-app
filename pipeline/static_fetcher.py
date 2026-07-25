"""Per-agency static GTFS refresh orchestrator.

Resolves the agency's static_strategy + static_url from the DB, dispatches to
the strategy, and on a fresh zip calls pipeline.static_loader.load_static.
"""

import logging
import pathlib
from typing import Optional

from pipeline.static_loader import load_static
from pipeline.strategies import get_static_strategy

logger = logging.getLogger(__name__)


def refresh_static(agency_id: int, conn, dest_dir: pathlib.Path) -> Optional[pathlib.Path]:
    """Refresh static GTFS for one agency. Returns the loaded zip path or None."""
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
    zip_path = strategy.fetch(agency_id, static_url, dest_dir)
    if zip_path is None:
        return None

    load_static(str(zip_path), agency_id, conn)
    logger.info(f"[static_fetcher] agency={agency_id} loaded {zip_path.name}")
    return zip_path


def refresh_all(conn, dest_dir: pathlib.Path) -> tuple[int, int, list[int]]:
    """Refresh static for every agency that has both static_url and static_strategy.

    Run-all-then-report, matching cmd_analyze_all/cmd_ingest_live: one
    agency's failure (network error, a rejected static_url, a corrupt zip)
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
            if refresh_static(aid, conn, dest_dir):
                n_loaded += 1
        except Exception:
            logger.exception(f"[static_fetcher] refresh failed for agency {aid}")
            conn.rollback()
            failed.append(aid)
    return n_loaded, len(ids), failed
