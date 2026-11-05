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
"""

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from pipeline import agg_sql, ch_aggregates, db, headways, static_archive, static_headways
from pipeline.db import build_stop_visits_ch_sql

# Versions of the tables built in the loops below, which default to 1. A bump
# goes here rather than into a second entry, which _index would reject.
_VERSIONS: dict[str, int] = {}

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


def _monthly(name: str) -> Callable[[], list[str]]:
    # An incremental build copies a month's unchanged days forward in
    # _build_month before adding the recomputed ones, so that is rendered too.
    return lambda: [ch_aggregates.select_sql(name, _RENDER_MONTH)[0], inspect.getsource(ch_aggregates._build_month)]


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
        agg_sql.route_headway_daily_ch_sql(""),
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


def _timetable_of(name: str) -> Timetable:
    return Timetable.ARCHIVED if name in ch_aggregates.STATIC_JOINED_TABLES else Timetable.NONE


def _input_of(name: str) -> ArchivedInput | None:
    return ArchivedInput.STOP_TIMES if name in ch_aggregates.STATIC_JOINED_TABLES else None


def _entries() -> list[AggTable]:
    unknown = set(_VERSIONS) - {*ch_aggregates.MONTHLY_TABLES, *ch_aggregates.WHOLE_HISTORY_TABLES}
    if unknown:
        raise ValueError(f"_VERSIONS names tables not built in its loops: {', '.join(sorted(unknown))}")
    entries = [
        AggTable("agg_feed_health", 1, Kind.DATED, Timetable.NONE, lambda: [agg_sql.feed_health_ch_sql(""), _clamp()]),
        AggTable(
            "agg_service_delivered_daily", 1, Kind.DATED, Timetable.NONE, lambda: [agg_sql.service_delivered_ch_sql("")]
        ),
        AggTable(
            "agg_schedule_revision_daily", 1, Kind.DATED, Timetable.NONE, lambda: [agg_sql.schedule_revision_ch_sql("")]
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
            1,
            Kind.DATED,
            Timetable.ARCHIVED,
            _headway_daily,
            # Its thresholds are the medians agg_route_headway's SQL keeps per
            # timetable, keyed by that table's version, not its rows.
            versions_from=("agg_route_headway",),
            archived_input=ArchivedInput.MEDIANS,
        ),
        AggTable(
            "agg_route_daily_dwell_run",
            1,
            Kind.DATED,
            Timetable.ARCHIVED,
            lambda: [agg_sql.dwell_run_sql(), build_stop_visits_ch_sql(extra_where="", join_schedule=True)],
            archived_input=ArchivedInput.STOP_TIMES,
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
