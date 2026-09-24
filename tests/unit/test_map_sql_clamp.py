"""Shared-shape guard: every hand-rolled ClickHouse dedup query behind the
Map tab clamps `dep_delay` to `pipeline.db.MAX_PLAUSIBLE_DELAY_SEC`, the same
ceiling `build_dedup_ch_sql` applies to every aggregated surface. A frozen or
stale GTFS-RT feed can corrupt `dep_delay` on any of these paths exactly as it
can on the ones that already went through the shared builder (see that
constant's own docstring in pipeline/db.py) -- map and aggregates must agree
on plausibility.
"""

from api.routers.map import _LIVE_DELAYS_DEDUP_SQL, _ROUTE_STOP_PROFILE_DEDUP_SQL, build_route_trips_sql
from pipeline.db import MAX_PLAUSIBLE_DELAY_SEC
from pipeline.reports.map import _route_shape_stats_dedup_sql, _route_shape_vote_dedup_sql

_CLAMP = f"BETWEEN -{MAX_PLAUSIBLE_DELAY_SEC} AND {MAX_PLAUSIBLE_DELAY_SEC}"


def test_live_delays_dedup_clamps_dep_delay():
    assert _CLAMP in _LIVE_DELAYS_DEDUP_SQL


def test_route_stop_profile_dedup_clamps_dep_delay():
    assert _CLAMP in _ROUTE_STOP_PROFILE_DEDUP_SQL


def test_route_trips_dedup_clamps_dep_delay():
    sql, _ = build_route_trips_sql("all")
    assert _CLAMP in sql


def test_route_shape_vote_dedup_clamps_dep_delay():
    assert _CLAMP in _route_shape_vote_dedup_sql("1")


def test_route_shape_stats_dedup_clamps_dep_delay():
    assert _CLAMP in _route_shape_stats_dedup_sql("1", "")
