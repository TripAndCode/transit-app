"""An agency's capability tier: which figures its feed can support.

The tier is derived, never hand-labelled, from two facts already stored per
agency: whether its ingest strategy's trip_ids are the timetable's
(:func:`pipeline.reports.comparability.trips_matchable`) and the RT
field-coverage gate (:func:`pipeline.strategies.static_join.coverage_gap`).

- ``basic``: the feed cannot support the timetable-joined figures (service
  delivered, dwell/running time, headway quality, observation coverage): its
  trips cannot be matched, its strategy cannot carry the RT-optional fields,
  or a live probe found them missing. Pages state the tier for those figures
  instead of a pipeline state.
- ``full``: nothing known about the feed rules those figures out. A figure
  may still be withheld for a pipeline state (a probe that lapsed or never
  ran), which is a fact about the pipeline, not the feed, and is stated as
  such.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any, Literal

from pipeline.reports.comparability import trips_matchable
from pipeline.strategies.static_join import coverage_gap

CapabilityTier = Literal["full", "basic"]

_FEED_CANNOT_SUPPORT = frozenset({"feed_unsupported", "probe_refuted"})


def capability_tier(ingest_strategy: str | None, rows: Iterable[Mapping[str, Any]], now: datetime) -> CapabilityTier:
    """The tier of an agency whose ``rt_field_coverage_probes`` rows are
    *rows*, judged at the database's *now*, as ``coverage_gap`` judges them."""
    if not trips_matchable(ingest_strategy):
        return "basic"
    gap = coverage_gap(ingest_strategy, rows, now)
    return "basic" if gap is not None and gap.reason in _FEED_CANNOT_SUPPORT else "full"


_TIER_STATE_SQL = """
    SELECT a.agency_id, a.ingest_strategy, now() AS now,
           p.field_name, p.confirmed, p.probed_at, p.expires_at
    FROM agencies a
    LEFT JOIN rt_field_coverage_probes p ON p.agency_id = a.agency_id
    WHERE a.agency_id = ANY($1::int[])
"""


async def capability_tiers(conn, agency_ids: Iterable[int]) -> dict[int, CapabilityTier]:
    """:func:`capability_tier` for each of *agency_ids*, in one round trip.
    An id with no agency row is left out."""
    ids = list(agency_ids)
    if not ids:
        return {}
    strategy: dict[int, str | None] = {}
    probes: dict[int, list[Mapping[str, Any]]] = {}
    now: datetime | None = None
    for r in await conn.fetch(_TIER_STATE_SQL, ids):
        aid = r["agency_id"]
        strategy[aid] = r["ingest_strategy"]
        now = r["now"]
        rows = probes.setdefault(aid, [])
        if r["field_name"] is not None:
            rows.append(r)
    if now is None:
        return {}
    return {aid: capability_tier(strategy[aid], probes[aid], now) for aid in strategy}
