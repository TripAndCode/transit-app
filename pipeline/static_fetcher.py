"""Per-agency static GTFS refresh orchestrator.

Resolves the agency's static_strategy + static_url from the DB, dispatches to
the strategy, and on a fresh zip calls pipeline.static_loader.load_static.
"""

import pathlib
from typing import Optional

from pipeline.static_loader import load_static
from pipeline.strategies import get_static_strategy


def refresh_static(agency_id: int, conn, dest_dir: pathlib.Path) -> Optional[pathlib.Path]:
    """Refresh static GTFS for one agency. Returns the loaded zip path or None."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT static_url, static_strategy FROM agencies WHERE agency_id = %s",
            (agency_id,),
        )
        row = cur.fetchone()
    if row is None:
        print(f"[static_fetcher] no agency {agency_id}")
        return None
    static_url, strategy_name = row
    if not static_url or not strategy_name:
        print(f"[static_fetcher] agency={agency_id} not configured for static refresh")
        return None

    strategy = get_static_strategy(strategy_name)
    zip_path = strategy.fetch(agency_id, static_url, dest_dir)
    if zip_path is None:
        return None

    load_static(str(zip_path), agency_id, conn)
    print(f"[static_fetcher] agency={agency_id} loaded {zip_path.name}")
    return zip_path


def refresh_all(conn, dest_dir: pathlib.Path) -> int:
    """Refresh static for every agency that has both static_url and static_strategy."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT agency_id FROM agencies "
            "WHERE static_url IS NOT NULL AND static_strategy IS NOT NULL "
            "ORDER BY agency_id"
        )
        ids = [r[0] for r in cur.fetchall()]
    n_loaded = 0
    for aid in ids:
        if refresh_static(aid, conn, dest_dir):
            n_loaded += 1
    return n_loaded
