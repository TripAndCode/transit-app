"""Ingest and static strategies.

Each ingest strategy module exposes:
    parse_feed(pb_bytes: bytes, agency_id: int, conn) -> list[tuple]
        Returns rows ready for pipeline.clickhouse.insert_updates (15-tuple,
        see pipeline.clickhouse.UPDATE_COLUMNS — agency_id is prepended by
        insert_updates, not part of this tuple). A strategy whose source
        feed doesn't populate one of the trailing fields (stop_id, arr_delay,
        schedule_relationship_trip, schedule_relationship_stop,
        feed_timestamp, scheduled_sec, static_version_id) passes NULL for it
        rather than guessing; a strategy's tuple shorter than 15 elements
        (predating one of these fields) is right-padded with None by
        insert_updates instead.
    REQUIRES_STATIC_TIMETABLE: bool (optional, False when absent)
        True when parse_feed takes each row's schedule from the agency's
        loaded static timetable as the row is read (static_join). A row read
        before any timetable is loaded is stored with no schedule and its
        file counted as done, so loading one later repairs nothing by itself
        (scripts/repair_archive_rows.py refills such stored rows). Every
        ingest path therefore refuses such an agency while it has no
        timetable loaded (pipeline.ingest.require_timetable), before reading
        or fetching anything, and the CLI exits 78 for it. This is the
        canonical statement of that rule; docs and scripts point here.

Each static strategy module exposes:
    fetch(agency_id: int, static_url: str, dest_dir: pathlib.Path, *, force: bool = False)
            -> Optional[pathlib.Path]
        Returns the path of a freshly persisted GTFS zip ready for load_static,
        or None when the source has nothing newer. Raises
        pipeline.strategies.errors.StaticFetchError when the fetch failed, so
        a dead source never reads as an unchanged one. "Newer" is measured
        against the change manifest record_loaded keeps on disk; ``force``
        sets it aside, so any zip the source serves is returned.
    record_loaded(agency_id: int, dest_dir: pathlib.Path, zip_path: pathlib.Path) -> None
        Called once load_static has committed the zip fetch() returned. "No
        change" means no change since the last zip that loaded, so a strategy
        records what it compares against here, never in fetch(): a zip whose
        load failed is then fetched as new again.

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
