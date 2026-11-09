"""Shared-shape guard: every hand-rolled ClickHouse dedup query behind the
Map tab clamps `dep_delay` to `pipeline.db.MAX_PLAUSIBLE_DELAY_SEC`, the same
ceiling `build_dedup_ch_sql` applies to every aggregated surface. A frozen or
stale GTFS-RT feed can corrupt `dep_delay` on any of these paths exactly as it
can on the ones that already went through the shared builder (see that
constant's own docstring in pipeline/db.py) -- map and aggregates must agree
on plausibility.
"""

import pathlib

import pytest

from api.range import UpdatesFilter
from api.routers import map as api_map
from api.routers.map import build_route_stop_profile_sql, build_route_trips_sql, live_delays_sql
from pipeline.db import MAX_PLAUSIBLE_DELAY_SEC
from pipeline.reports import map as reports_map
from pipeline.reports.map import _route_shape_stats_dedup_sql, _route_shape_vote_dedup_sql
from pipeline.static_archive import Stamp

_CLAMP = f"BETWEEN -{MAX_PLAUSIBLE_DELAY_SEC} AND {MAX_PLAUSIBLE_DELAY_SEC}"
_ANY_DAY = UpdatesFilter("1", "", {})
# Each query files its rows under a stamp, and every stamp keeps the clamp.
_STAMPS = pytest.mark.parametrize("stamp", list(Stamp))


@_STAMPS
def test_live_delays_dedup_clamps_dep_delay(stamp):
    assert _CLAMP in live_delays_sql(stamp)


@_STAMPS
def test_route_stop_profile_dedup_clamps_dep_delay(stamp):
    assert _CLAMP in build_route_stop_profile_sql("updates", stamp)


@_STAMPS
def test_route_trips_dedup_clamps_dep_delay(stamp):
    sql, _ = build_route_trips_sql("all", stamp=stamp)
    assert _CLAMP in sql


@_STAMPS
def test_route_shape_vote_dedup_clamps_dep_delay(stamp):
    assert _CLAMP in _route_shape_vote_dedup_sql(_ANY_DAY, stamp)


@_STAMPS
def test_route_shape_stats_dedup_clamps_dep_delay(stamp):
    assert _CLAMP in _route_shape_stats_dedup_sql(_ANY_DAY, "", stamp)


# ── The five above are the ones that exist today. This one is about the sixth.


_FACT_READS = ("updates", "{table}", "observation_rows(", "_route_rows(")
# A literal reading rows through one of these is clamped by it: the tests
# above render both modules' _route_rows under every stamp.
_CLAMPED_READS = ("MAX_PLAUSIBLE_DELAY_SEC", _CLAMP, "observation_filter_ch(", "_route_rows(")

_SQL_SOURCES = (
    pathlib.Path(api_map.__file__),
    pathlib.Path(reports_map.__file__),
)


def _sql_literals(source: str) -> list[str]:
    """Every triple-quoted literal in *source* that queries the fact table.

    Read as text rather than by importing and inspecting objects, because a
    new query can arrive as a module constant, a function-local string or an
    f-string, and only the text form catches all three. The fact table
    arrives literally (``updates``), as the ``{table}`` placeholder
    `live_table_for` fills, or as the rows `observation_rows` files, called
    directly or through a module's ``_route_rows``, so any of those spellings
    counts as a match.
    """
    literals: list[str] = []
    for chunk in source.split('"""')[1::2]:
        if any(read in chunk for read in _FACT_READS) and "dep_delay" in chunk:
            literals.append(chunk)
    return literals


def test_every_fact_table_query_in_the_map_modules_clamps_dep_delay():
    """The tests above pin the five hand-rolled dedups that exist now. This
    one is what makes a *new* one fail instead of quietly shipping unclamped.

    The clamp is not a property of these five queries, it is a property of
    reading `dep_delay` off `updates` at all -- a frozen or stale feed
    corrupts the column the same way whichever query reads it. Pinning only
    the known instances guards the instances, not the rule.
    """
    # Matched in source form, not rendered: these are f-strings, so the
    # clamp appears as the interpolation rather than as the number.
    unclamped = []
    for path in _SQL_SOURCES:
        for literal in _sql_literals(path.read_text()):
            if not any(clamp in literal for clamp in _CLAMPED_READS):
                first_line = next((ln.strip() for ln in literal.splitlines() if ln.strip()), "")
                unclamped.append(f"{path.name}: {first_line[:70]}")
    assert unclamped == [], (
        "these queries read dep_delay off `updates` without the plausibility "
        f"clamp -- use build_dedup_ch_sql or add it explicitly: {unclamped}"
    )


def test_the_literal_scan_itself_finds_the_known_queries():
    """Canary: if the scan stops matching, the guard above passes having
    inspected nothing."""
    found = sum(len(_sql_literals(path.read_text())) for path in _SQL_SOURCES)
    assert found >= 5, f"expected at least the five known dedups, scanned {found}"
