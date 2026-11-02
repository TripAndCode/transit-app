"""Every aggregate analyze builds: its logic version, its kind, whether it reads
the loaded timetable, and the tables it is computed from.

A table's version declares the generation of its builder. Bump it in the same
commit as any change to what the builder produces: its SQL, a threshold, a
bucket width, or the Python that shapes its rows. `render()` returns exactly
that text, and tests/unit/test_agg_registry_versions.py fails when it changes
while the version does not.
"""

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from pipeline import agg_sql, ch_aggregates, headways
from pipeline.db import build_stop_visits_ch_sql

# Any fixed month renders a monthly table's statement: only its date bounds
# depend on the month, and those are bound parameters.
_RENDER_MONTH = 202601


class Kind(str, Enum):
    DATED = "dated"  # rows keyed by service date, rebuilt per changed date
    WHOLE = "whole"  # whole history, finalized from an internal ClickHouse table
    STATIC_ONLY = "static_only"  # built from the loaded timetable alone


@dataclass(frozen=True)
class AggTable:
    name: str
    version: int
    kind: Kind
    reads_static: bool
    render: Callable[[], list[str]]
    depends_on: tuple[str, ...] = ()


def _monthly(name: str) -> Callable[[], list[str]]:
    return lambda: [ch_aggregates.select_sql(name, _RENDER_MONTH)[0]]


def _whole(name: str) -> Callable[[], list[str]]:
    if name == "agg_stop_routes":
        return lambda: [ch_aggregates.whole_history_sql(name), agg_sql.STOP_ROUTES_ORDER_SQL]
    return lambda: [ch_aggregates.whole_history_sql(name)]


def _headway_daily() -> list[str]:
    return [
        agg_sql.route_headway_daily_ch_sql(""),
        inspect.getsource(headways.reconstruct_headways),
        inspect.getsource(headways.pool_daily_gaps),
        inspect.getsource(headways.daily_headway_rows),
        inspect.getsource(headways.count_long_gaps),
        f"LONG_GAP_MULTIPLIER={headways.LONG_GAP_MULTIPLIER!r}",
    ]


def _entries() -> list[AggTable]:
    entries = [
        AggTable("agg_feed_health", 1, Kind.DATED, False, lambda: [agg_sql.feed_health_ch_sql("")]),
        AggTable("agg_service_delivered_daily", 1, Kind.DATED, False, lambda: [agg_sql.service_delivered_ch_sql("")]),
        AggTable("agg_schedule_revision_daily", 1, Kind.DATED, False, lambda: [agg_sql.schedule_revision_ch_sql("")]),
        AggTable("agg_route_headway", 1, Kind.STATIC_ONLY, True, lambda: [agg_sql.route_headway_sql()]),
        AggTable(
            "agg_route_headway_daily",
            1,
            Kind.DATED,
            True,
            _headway_daily,
            depends_on=("agg_route_headway",),
        ),
        AggTable(
            "agg_route_daily_dwell_run",
            1,
            Kind.DATED,
            True,
            lambda: [agg_sql.dwell_run_sql(), build_stop_visits_ch_sql(extra_where="")],
        ),
    ]
    for name in ch_aggregates.MONTHLY_TABLES:
        entries.append(AggTable(name, 1, Kind.DATED, name in ch_aggregates.STATIC_JOINED_TABLES, _monthly(name)))
    for name in ch_aggregates.WHOLE_HISTORY_TABLES:
        entries.append(
            AggTable(
                name,
                1,
                Kind.WHOLE,
                name in ch_aggregates.STATIC_JOINED_TABLES,
                _whole(name),
                depends_on=(ch_aggregates.whole_history_source(name),),
            )
        )
    return entries


TABLES: dict[str, AggTable] = {t.name: t for t in _entries()}
