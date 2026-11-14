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
from pipeline.strategies.static_join import coverage_gap, rt_field_coverage_states

CapabilityTier = Literal["full", "basic"]

_FEED_CANNOT_SUPPORT = frozenset({"feed_unsupported", "probe_refuted"})


def capability_tier(ingest_strategy: str | None, rows: Iterable[Mapping[str, Any]], now: datetime) -> CapabilityTier:
    """The tier of an agency whose ``rt_field_coverage_probes`` rows are
    *rows*, judged at the database's *now*, as ``coverage_gap`` judges them."""
    if not trips_matchable(ingest_strategy):
        return "basic"
    gap = coverage_gap(ingest_strategy, rows, now)
    return "basic" if gap is not None and gap.reason in _FEED_CANNOT_SUPPORT else "full"


async def capability_tiers(conn, agency_ids: Iterable[int]) -> dict[int, CapabilityTier]:
    """:func:`capability_tier` for each of *agency_ids*, in one round trip.
    An id with no agency row is left out."""
    states = await rt_field_coverage_states(conn, agency_ids)
    return {aid: capability_tier(s.ingest_strategy, s.probes, s.now) for aid, s in states.items()}
