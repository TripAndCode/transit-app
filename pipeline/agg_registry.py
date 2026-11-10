"""Every aggregate analyze builds: its logic version, its kind, which timetable
it reads, and the tables it is computed from.

A table's version declares the generation of its builder. Bump it in the same
commit as any change to what the builder produces: its SQL, a threshold, a
bucket width, or the Python that shapes its rows. analyze records each
table's fingerprint, which folds in this version, in agg_table_state, and
rebuilds the table in full, with the tables computed from it, when the
fingerprint changes. `render()` returns exactly that text, and
tests/unit/test_agg_registry_versions.py fails when it changes while the
version does not.

The method as a whole has one version, METHOD_VERSION, which every exported
figure names. A table's version bump or a clamp change is a new method
version (METHOD_VERSIONS).
"""

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import NamedTuple

from pipeline import agg_sql, ch_aggregates, db, headways, static_archive, static_headways
from pipeline.db import build_stop_visits_ch_sql
from pipeline.static_archive import Stamp

# Versions of the tables built in the loops below, which default to 1. A bump
# goes here rather than into a second entry, which _index would reject.
_VERSIONS: dict[str, int] = {
    "agg_daily_trend": 3,
    "agg_hour_daily": 4,
    "agg_route_daily": 3,
    "agg_route_daily_dist": 3,
    "agg_route_hour": 4,
    "agg_route_hour_dow": 4,
    "agg_route_stats": 3,
    "agg_route_stop_daily": 4,
    "agg_stop_daily": 4,
    "agg_stop_routes": 2,
    "route_delay_counts": 4,
    "stop_route_days": 2,
}

# Any fixed month renders a monthly table's statement: only its date bounds
# depend on the month, and those are bound parameters.
_RENDER_MONTH = 202601


class Kind(str, Enum):
    DATED = "dated"  # rows keyed by service date, rebuilt per changed date
    WHOLE = "whole"  # whole history, finalized from an internal ClickHouse table
    STATIC_ONLY = "static_only"  # built from the loaded timetable alone


class Timetable(str, Enum):
    """Which timetable a table's rows are built from, which decides what of it
    the table's fingerprint carries (pipeline.analyze.table_fingerprints)."""

    NONE = "none"  # no timetable
    LOADED = "loaded"  # the timetable loaded now: a new one rebuilds the table
    # Each row's own archived timetable (pipeline.static_archive): a new loaded
    # timetable changes no built row, except where the ingest stamps no version
    # and every row falls back to the loaded one.
    ARCHIVED = "archived"


class ArchivedInput(str, Enum):
    """What an archive-joined table reads of the timetable each of its rows
    joins, which decides the dates a new loaded timetable reaches
    (pipeline.analyze._dates_the_move_reaches)."""

    STOP_TIMES = "stop_times"  # its trip's archived stop times
    MEDIANS = "medians"  # its route's kept scheduled headway median (pipeline.static_headways)


@dataclass(frozen=True)
class AggTable:
    name: str
    version: int
    kind: Kind
    timetable: Timetable
    render: Callable[[], list[str]]
    depends_on: tuple[str, ...] = ()
    # Tables whose logic version this one's rows depend on without being
    # computed from their rows: folded into its fingerprint, so their bump
    # rebuilds it, and nothing else of theirs does.
    versions_from: tuple[str, ...] = ()
    archived_input: ArchivedInput | None = None
    # Its rows are read filed under the timetable in force on their service
    # date (static_archive.Stamp), so a date whose version in force moved is
    # rebuilt (pipeline.analyze), and so is the table when a version's content
    # changes under its name (static_archive.clear_archive_joined_states).
    in_force: bool = False


def _monthly(name: str) -> Callable[[], list[str]]:
    # An incremental build copies a month's unchanged days forward in
    # _build_month before adding the recomputed ones, so that is rendered too,
    # and so is how a build picks the timetable its rows are filed under.
    return lambda: [
        *(ch_aggregates.select_sql(name, _RENDER_MONTH, stamp=stamp)[0] for stamp in Stamp),
        inspect.getsource(ch_aggregates._build_month),
        inspect.getsource(ch_aggregates.stamp_for),
    ]


def _whole(name: str) -> Callable[[], list[str]]:
    # The build reads the internal table through _internal_source, which stitches
    # the months a run rebuilt onto the rest; whole_history_sql alone recomputes
    # them inline, so the stitching is rendered beside it.
    def render() -> list[str]:
        parts = [ch_aggregates.whole_history_sql(name), inspect.getsource(ch_aggregates._internal_source)]
        if name == "agg_stop_routes":
            parts.append(agg_sql.STOP_ROUTES_ORDER_SQL)
        return parts

    return render


def _clamp() -> str:
    # Bound as a query parameter by the builders that read it, so it never
    # appears in their SQL text.
    return f"MAX_PLAUSIBLE_DELAY_SEC={db.MAX_PLAUSIBLE_DELAY_SEC!r}"


def _headway_daily() -> list[str]:
    return [
        *(agg_sql.route_headway_daily_ch_sql("", stamp) for stamp in Stamp),
        _clamp(),
        inspect.getsource(headways.reconstruct_headways),
        inspect.getsource(headways.pool_daily_gaps),
        inspect.getsource(headways.dominant_contents),
        inspect.getsource(static_archive.ScheduleJoin.content_of),
        inspect.getsource(headways.thresholds),
        inspect.getsource(static_headways.route_medians),
        inspect.getsource(headways.daily_headway_rows),
        inspect.getsource(headways.count_long_gaps),
        f"LONG_GAP_MULTIPLIER={headways.LONG_GAP_MULTIPLIER!r}",
    ]


def _feed_quality() -> list[str]:
    # Imported here: the scorecard reads pipeline.reports, whose package
    # imports the report stack, and that reads METHOD_VERSION from this module.
    from pipeline import feed_quality

    return feed_quality.render()


def _timetable_of(name: str) -> Timetable:
    return Timetable.ARCHIVED if name in ch_aggregates.STATIC_JOINED_TABLES else Timetable.NONE


def _input_of(name: str) -> ArchivedInput | None:
    return ArchivedInput.STOP_TIMES if name in ch_aggregates.STATIC_JOINED_TABLES else None


def _entries() -> list[AggTable]:
    unknown = set(_VERSIONS) - {*ch_aggregates.MONTHLY_TABLES, *ch_aggregates.WHOLE_HISTORY_TABLES}
    if unknown:
        raise ValueError(f"_VERSIONS names tables not built in its loops: {', '.join(sorted(unknown))}")
    entries = [
        AggTable("agg_feed_health", 2, Kind.DATED, Timetable.NONE, lambda: [agg_sql.feed_health_ch_sql(""), _clamp()]),
        AggTable(
            "agg_service_delivered_daily", 3, Kind.DATED, Timetable.NONE, lambda: [agg_sql.service_delivered_ch_sql("")]
        ),
        AggTable(
            "agg_schedule_revision_daily",
            2,
            Kind.DATED,
            Timetable.NONE,
            lambda: [agg_sql.schedule_revision_ch_sql("", stamp) for stamp in (Stamp.INGESTED, Stamp.IN_FORCE)],
            in_force=True,
        ),
        AggTable(
            "agg_route_headway",
            1,
            Kind.STATIC_ONLY,
            Timetable.LOADED,
            # pipeline.static_headways keeps each timetable's medians under this
            # version, so the SQL computing them is rendered here too.
            lambda: [agg_sql.route_headway_sql(), agg_sql.route_headway_sql(all_routes=True)],
        ),
        AggTable(
            "agg_route_headway_daily",
            3,
            Kind.DATED,
            Timetable.ARCHIVED,
            _headway_daily,
            # Its thresholds are the medians agg_route_headway's SQL keeps per
            # timetable, keyed by that table's version, not its rows.
            versions_from=("agg_route_headway",),
            archived_input=ArchivedInput.MEDIANS,
            in_force=True,
        ),
        AggTable(
            "agg_route_daily_dwell_run",
            3,
            Kind.DATED,
            Timetable.ARCHIVED,
            lambda: [
                agg_sql.dwell_run_sql(),
                *(build_stop_visits_ch_sql(extra_where="", join_schedule=True, stamp=stamp) for stamp in Stamp),
            ],
            archived_input=ArchivedInput.STOP_TIMES,
            in_force=True,
        ),
        AggTable(
            "feed_quality_monthly",
            2,
            Kind.DATED,
            # A planned day no capture saved reads the loaded timetable's plan.
            Timetable.LOADED,
            _feed_quality,
            depends_on=("agg_route_daily", "agg_feed_health"),
        ),
    ]
    for name in ch_aggregates.MONTHLY_TABLES:
        entries.append(
            AggTable(
                name,
                _VERSIONS.get(name, 1),
                Kind.DATED,
                _timetable_of(name),
                _monthly(name),
                archived_input=_input_of(name),
                in_force=True,
            )
        )
    for name in ch_aggregates.WHOLE_HISTORY_TABLES:
        entries.append(
            AggTable(
                name,
                _VERSIONS.get(name, 1),
                Kind.WHOLE,
                _timetable_of(name),
                _whole(name),
                depends_on=(ch_aggregates.whole_history_source(name),),
                archived_input=_input_of(name),
                in_force=True,
            )
        )
    return entries


def _index(entries: list[AggTable]) -> dict[str, AggTable]:
    tables: dict[str, AggTable] = {}
    for entry in entries:
        if entry.name in tables:
            raise ValueError(f"{entry.name} is registered twice")
        if (entry.timetable is Timetable.ARCHIVED) != (entry.archived_input is not None):
            raise ValueError(f"{entry.name} must name what it reads of its timetable exactly when it joins the archive")
        tables[entry.name] = entry
    return tables


TABLES: dict[str, AggTable] = _index(_entries())


def method_inputs() -> dict[str, int]:
    """What the analysis method is made of now: every table's logic version
    and the plausibility clamp, the one constant every table's fingerprint
    folds in (pipeline.analyze.table_fingerprints). The ingest strategy and the
    timetable an agency's fingerprints also carry describe its data, not the
    method."""
    return {"MAX_PLAUSIBLE_DELAY_SEC": db.MAX_PLAUSIBLE_DELAY_SEC} | {name: t.version for name, t in TABLES.items()}


class MethodVersion(NamedTuple):
    version: str  # YYYY.M.N
    # What this version set of method_inputs(): every input for the first
    # version, and only those it changed for each later one. None retires a
    # table that is no longer built.
    sets: dict[str, int | None]


# Every version of the analysis method, oldest first. A change to
# method_inputs() appends a later version with what it sets, and its row to
# DELAY_ANALYSIS.md's "Method versions" table. A released entry never changes,
# because exports name it: tests/unit/method_versions.json pins each one, and
# tests/unit/test_method_version.py keeps the history, record and registry agreed.
METHOD_VERSIONS: tuple[MethodVersion, ...] = (
    MethodVersion(
        "2026.11.0",
        {
            "MAX_PLAUSIBLE_DELAY_SEC": 7200,
            "agg_daily_trend": 1,
            "agg_feed_health": 1,
            "agg_hour_daily": 1,
            "agg_route_daily": 1,
            "agg_route_daily_dist": 1,
            "agg_route_daily_dwell_run": 1,
            "agg_route_headway": 1,
            "agg_route_headway_daily": 1,
            "agg_route_hour": 1,
            "agg_route_hour_dow": 1,
            "agg_route_stats": 1,
            "agg_route_stop_daily": 1,
            "agg_schedule_revision_daily": 1,
            "agg_service_delivered_daily": 1,
            "agg_stop_daily": 1,
            "agg_stop_routes": 1,
            "route_delay_counts": 1,
            "stop_route_days": 1,
        },
    ),
    MethodVersion(
        "2026.11.1",
        {
            "agg_hour_daily": 2,
            "agg_route_hour": 2,
            "agg_route_hour_dow": 2,
            "agg_route_stop_daily": 2,
            "agg_stop_daily": 2,
            "route_delay_counts": 2,
        },
    ),
    MethodVersion("2026.11.2", {"agg_feed_health": 2}),
    MethodVersion(
        "2026.11.3",
        {
            "agg_daily_trend": 2,
            "agg_hour_daily": 3,
            "agg_route_daily": 2,
            "agg_route_daily_dist": 2,
            "agg_route_daily_dwell_run": 2,
            "agg_route_headway_daily": 2,
            "agg_route_hour": 3,
            "agg_route_hour_dow": 3,
            "agg_route_stats": 2,
            "agg_route_stop_daily": 3,
            "agg_schedule_revision_daily": 2,
            "agg_stop_daily": 3,
            "agg_stop_routes": 2,
            "route_delay_counts": 3,
            "stop_route_days": 2,
        },
    ),
    MethodVersion(
        "2026.11.4",
        {
            "agg_daily_trend": 3,
            "agg_hour_daily": 4,
            "agg_route_daily": 3,
            "agg_route_daily_dist": 3,
            "agg_route_daily_dwell_run": 3,
            "agg_route_headway_daily": 3,
            "agg_route_hour": 4,
            "agg_route_hour_dow": 4,
            "agg_route_stats": 3,
            "agg_route_stop_daily": 4,
            "agg_service_delivered_daily": 2,
            "agg_stop_daily": 4,
            "route_delay_counts": 4,
        },
    ),
    MethodVersion("2026.11.5", {"feed_quality_monthly": 1}),
    MethodVersion("2026.11.6", {"feed_quality_monthly": 2}),
    MethodVersion("2026.11.7", {"agg_service_delivered_daily": 3}),
)

# The method every report, CSV and council document names beside its figures
# (pipeline.reports.definition.DefinitionMeta).
METHOD_VERSION = METHOD_VERSIONS[-1].version


def table_scopes(
    current: dict[str, str],
    stored: dict[str, str],
    ledger_dates: list | None,
    extra_dates: dict[str, list] | None = None,
) -> tuple[dict[str, list | None], dict[str, str]]:
    """Each table's rebuild scope, and why each table rebuilt in full is.

    A scope is None for every date (the whole table, for one with no date), a
    list of the dates to rebuild, or [] for nothing. A table goes in full when
    there is no ledger, when it has no stored fingerprint or a different one,
    or when a table it is computed from goes in full. A dated table otherwise
    takes the ledger's changed dates, and *extra_dates*' for it; a
    whole-history table is rebuilt when its source rebuilds any date; a table
    built from the timetable alone stands.
    """
    reasons: dict[str, str] = {}
    for name in TABLES:
        if ledger_dates is None:
            reasons[name] = "no ledger"
        elif name not in stored:
            reasons[name] = "new"
        elif stored[name] != current[name]:
            reasons[name] = "fingerprint changed"
    grew = True
    while grew:
        grew = False
        for name, table in TABLES.items():
            if name in reasons:
                continue
            upstream = next((d for d in table.depends_on if d in reasons), None)
            if upstream is not None:
                reasons[name] = f"depends on {upstream}"
                grew = True
    scopes: dict[str, list | None] = {}
    for name, table in TABLES.items():
        if name in reasons:
            scopes[name] = None
        elif table.kind is Kind.DATED:
            scopes[name] = sorted({*(ledger_dates or []), *(extra_dates or {}).get(name, [])})
        elif table.kind is Kind.STATIC_ONLY:
            scopes[name] = []
    for name, table in TABLES.items():
        if table.kind is Kind.WHOLE and name not in scopes:
            if any(scopes[source] != [] for source in table.depends_on):
                scopes[name] = None
                reasons[name] = "source changed"
            else:
                scopes[name] = []
    return scopes, reasons
