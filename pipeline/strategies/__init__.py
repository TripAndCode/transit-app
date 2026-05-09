"""Ingest and static strategies.

Each ingest strategy module exposes:
    parse_feed(pb_bytes: bytes, agency_id: int, conn) -> list[tuple]
        Returns rows ready for the standard updates INSERT (9-tuple, see
        pipeline.strategies._pb.UPDATE_INSERT_SQL).

Each static strategy module exposes:
    fetch(agency_id: int, conn, dest_dir: pathlib.Path) -> Optional[pathlib.Path]
        Returns the path of a freshly persisted GTFS zip ready for load_static,
        or None if no change.

Strategies are resolved by name via STRATEGIES below.
"""

import importlib


def get_ingest_strategy(name: str):
    if not name:
        # back-compat: empty / NULL falls back to Aomori for the single
        # existing production agency.
        name = "aomori_regex"
    return importlib.import_module(f"pipeline.strategies.{name}")


def get_static_strategy(name: str):
    if not name:
        return None  # caller treats "no static strategy" as a skip
    return importlib.import_module(f"pipeline.strategies.{name}")
