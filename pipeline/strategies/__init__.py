"""Ingest and static strategies.

Each ingest strategy module exposes:
    parse_feed(pb_bytes: bytes, agency_id: int, conn) -> list[tuple]
        Returns rows ready for pipeline.clickhouse.insert_updates (13-tuple,
        see pipeline.clickhouse.UPDATE_COLUMNS — agency_id is prepended by
        insert_updates, not part of this tuple). A strategy whose source
        feed doesn't populate one of the trailing fields (stop_id, arr_delay,
        schedule_relationship_trip, schedule_relationship_stop,
        feed_timestamp) passes NULL for it rather than guessing.

Each static strategy module exposes:
    fetch(agency_id: int, conn, dest_dir: pathlib.Path) -> Optional[pathlib.Path]
        Returns the path of a freshly persisted GTFS zip ready for load_static,
        or None if no change.

Strategies are resolved by name via STRATEGIES below.
"""

import importlib


def get_ingest_strategy(name: str):
    """Return the ingest strategy module for the given strategy name.

    Falls back to aomori_regex for empty/NULL names (back-compat for the
    single existing production agency that predates the strategy column).
    """
    if not name:
        name = "aomori_regex"
    return importlib.import_module(f"pipeline.strategies.{name}")


def get_static_strategy(name: str):
    """Return the static strategy module for the given strategy name, or None if not set."""
    if not name:
        return None  # caller treats "no static strategy" as a skip
    return importlib.import_module(f"pipeline.strategies.{name}")
