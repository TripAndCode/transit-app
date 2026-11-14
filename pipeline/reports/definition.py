"""Shared "definition metadata" for on-time/late report and comparison views.

A user comparing two agencies' (or two exports') on-time rates needs to see
they're using the same underlying definition -- which early/late tolerance
was applied, where it was measured, how duplicate observations were
resolved, and which readings were excluded as implausible. Building that
metadata here, from the tolerance constants in ``pipeline.histogram`` and
the shared dedup/exclusion rule in ``pipeline.db``, means every JSON
response and CSV export renders identical text for identical tolerance
settings -- none of them re-derive or hardcode the values themselves.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from itertools import groupby
from pathlib import Path
from typing import Literal

import asyncpg
from pydantic import AwareDatetime, BaseModel

from pipeline import static_archive
from pipeline.agg_registry import METHOD_VERSION
from pipeline.cache import clear_all, evict_for_rebuild
from pipeline.clickhouse import jst_date
from pipeline.db import MAX_PLAUSIBLE_DELAY_SEC
from pipeline.histogram import LEGACY_PRESET_NAME, LEGACY_SEVERE_LATE_TOLERANCE_SEC
from pipeline.reports.rankings import (
    DEFAULT_EARLY_TOLERANCE_SEC,
    ON_TIME_PRESETS,
    STANDARD_PRESET_NAME,
    early_departure_sec,
    on_time_window,
)
from pipeline.reports.savepoint import savepoint
from pipeline.reports.schedule_revision import ScheduleDays
from pipeline.timetable_in_force import fetch_day

# Stable identifiers (not resolved per request, unlike the tolerance fields
# below) so a caller can render/translate them instead of a fixed string
# baked into this module. ``pipeline.db.build_dedup_ch_sql`` implements the
# dedup rule itself (the latest timely observation per stop event wins, one
# received long after the departure being ignored); no separate
# "measurement point" knob exists elsewhere in the pipeline today.
DEDUP_RULE = "latest_observation_per_stop_event"
MEASUREMENT_POINT = "all_stops_all_observations"
# What every figure is made from: the delays the operator feed predicted for
# each stop, the last of each the dedup kept, never an observed passage.
# Observed values would be a second basis beside it, under their own identifier.
VALUE_BASIS: Literal["feed_prediction"] = "feed_prediction"

# Locale-aware operator-facing text for each identifier above, keyed by
# (value, locale) tuples rather than re-typed string literals -- a dict keyed
# by a bare name is resolved against that name's *current* value, so this
# can't silently fall out of sync with its constant the way independently
# hand-typed copies could. Both format_definition_csv_line (Japanese-only, for
# the CSV preamble cell) and format_definition_footnotes (locale-aware, for a
# report template's footnote lines) render from this ONE source of truth, so
# the two surfaces can never describe the same DefinitionMeta differently.
_MEASUREMENT_POINT_TEXT: dict[tuple[str, str], str] = {
    (MEASUREMENT_POINT, "ja"): "全停留所・全観測",
    (MEASUREMENT_POINT, "en"): "all stops, all observations",
}
_DEDUP_RULE_TEXT: dict[tuple[str, str], str] = {
    (DEDUP_RULE, "ja"): "停留所イベントごとに最新観測を採用（予定発車から130分を過ぎて届いた観測は除く）",
    (DEDUP_RULE, "en"): (
        "latest observation per stop event, leaving out observations received more than 130 minutes after "
        "the scheduled departure"
    ),
}
_VALUE_BASIS_TEXT: dict[tuple[str, str], str] = {
    (VALUE_BASIS, "ja"): "事業者のフィードが停留所ごとに最後に配信した遅延の予測値から算出（通過の観測値ではない）",
    (VALUE_BASIS, "en"): (
        "computed from the delays the operator's feed last predicted for each stop (not observed passing times)"
    ),
}

# format_definition_csv_line's own Japanese-only lookups: a plain
# {value: text} dict, so an unrecognized value raises KeyError, sourced from
# the locale-aware dicts above's "ja" slice instead of repeating either string
# a second time.
_MEASUREMENT_POINT_CSV_TEXT: dict[str, str] = {MEASUREMENT_POINT: _MEASUREMENT_POINT_TEXT[(MEASUREMENT_POINT, "ja")]}
_DEDUP_RULE_CSV_TEXT: dict[str, str] = {DEDUP_RULE: _DEDUP_RULE_TEXT[(DEDUP_RULE, "ja")]}

# Where the method every stamp names is published, per language: the
# document each export's definition and the app footer link to. The frontend
# file is the one home, read here as pipeline.data_sources reads its sources,
# so the server and the screens cannot link different documents.
METHODOLOGY_URLS: dict[str, str] = json.loads(
    (Path(__file__).resolve().parents[2] / "frontend" / "src" / "components" / "methodology.json").read_text(
        encoding="utf-8"
    )
)

# How many leading hex digits of a timetable's content hash the stamp shows:
# enough to tell an agency's timetables apart, short enough to read aloud.
CONTENT_HASH_DIGITS = 8

# A named preset in a reader's words, said beside its window wherever a
# definition is written out: the footnotes, the CSV preamble and, in the same
# words, the screens (frontend definitionMeta.preset).
_PRESET_TEXT: dict[tuple[str, str], str] = {
    (STANDARD_PRESET_NAME, "ja"): "業界標準",
    (STANDARD_PRESET_NAME, "en"): "industry standard",
    (LEGACY_PRESET_NAME, "ja"): "従来の基準",
    (LEGACY_PRESET_NAME, "en"): "legacy",
}


class TimetableVersion(BaseModel):
    """A timetable an agency's observations were ingested against, and on how
    many of a request's days it was the one most of them carried
    (agg_schedule_revision_daily). ``name`` is the loaded zip's version name,
    ``fetched_on`` the day that name says it was fetched; ``name`` is None for
    the days with no timetable recorded: none analyzed yet, or an ingest that
    stamps no version.

    ``content_hash`` is the first :data:`CONTENT_HASH_DIGITS` hex digits of
    the content the timetable archive maps the name to
    (:mod:`pipeline.static_archive`), read from the map's Postgres mirror: two
    names with one hash carried the same timetable, and a name refetched with
    other content shows the other hash. None for a name the archive does not
    map, which no hash is guessed for, and for the days with none recorded.
    Beside aggregates it is None as well while they hold figures built before
    the name was mapped to that content, until analyze rebuilds them. Beside a
    live scan it is the content the join that filed the scan's rows maps the
    name to (:func:`pipeline.live_filing.schedule_join`), and None for an
    agency whose ingest stamps no version, which has no such join."""

    agency_id: int
    name: str | None
    fetched_on: date | None
    days: int
    content_hash: str | None = None


class SourceMix(BaseModel):
    """How many of a request's days an agency's observations came mostly from
    the collector's archive, which holds every poll it made, or from the live
    path, which holds only what the app received in real time and so is the
    sparser of the two. The rest are the days with no source recorded: no
    observations, a day not analyzed yet, or one analyzed before sources were
    counted. The three add up to the range (:func:`_source_mix`)."""

    agency_id: int
    archive_days: int
    live_days: int
    none_days: int


#: Why a :class:`FeedQuality` coverage is None: ``no_data`` is the gate's
#: first check, no observation in the period's aggregates; the rest say why
#: coverage cannot be measured, in the agencies board's terms
#: (:func:`pipeline.reports.comparability.coverage_unknown_why`).
CoverageMissing = Literal["no_data", "unreadable", "unmatched", "no_plan", "no_plan_observed"]


class FeedQuality(BaseModel):
    """An agency's realtime completeness and clamp share over a request's
    range, for the whole agency whatever filter narrows the figures beside it,
    by the comparability gate's own rules (:mod:`pipeline.reports.comparability`)
    over the figures the agencies board reads
    (:func:`pipeline.reports.network.feed_quality_of`).

    ``coverage_pct`` is ``observed_trips`` of ``coverage_planned_trips``, the
    board's observation coverage, measured through the newest day any agency
    it lists has observations for; ``clamp_pct`` is the share of readings the
    plausibility clamp dropped. Each is None beside its reason, never 0:
    ``coverage_missing`` (:data:`CoverageMissing`) and ``clamp_missing``,
    ``no_data`` when no reading carried a delay. ``coverage_low`` and
    ``clamp_high`` are whether the gate flags the figure, judged as printed
    against ``min_coverage_pct`` and ``max_clamp_pct``, the thresholds in
    force when the stamp was made."""

    agency_id: int
    coverage_pct: float | None
    observed_trips: int | None
    coverage_planned_trips: int | None
    coverage_missing: CoverageMissing | None
    coverage_low: bool
    clamp_pct: float | None
    clamp_missing: Literal["no_data"] | None
    clamp_high: bool
    min_coverage_pct: float
    max_clamp_pct: float


class DefinitionMeta(BaseModel):
    """On-time/late definition actually used to produce a report or
    comparison view's numbers -- surfaced alongside any such view (and in
    its CSV/export output) so two views comparing on-time rates can be
    checked for an apples-to-apples definition instead of silently
    differing.

    ``preset``/``early_tolerance_sec``/``late_tolerance_sec`` are ``None``
    for report types with no on-time/late tolerance concept at all (e.g.
    ``ranking``, ``trend``) -- the dedup/exclusion/measurement-point fields
    still apply to those, since every aggregate shares the one dedup
    builder, but there is no tolerance value to show.

    ``early_departure_sec`` is how many seconds early a departure must be for
    the early-departure rate to count it
    (:func:`pipeline.reports.rankings.early_departure_sec`), on the reports
    that show that rate; ``None`` on every other.

    ``method_version`` names the analysis method the figures were made with
    (:data:`pipeline.agg_registry.METHOD_VERSION`). ``source`` says whether
    they came from the aggregates or from a live scan of the observations
    (:func:`pipeline.reports.rankings.report_reads_aggregates`). ``built_at``
    is when analyze last rewrote the aggregates behind them
    (:func:`stamp_build`), which a router with a connection fills in: always
    ``None`` for a live scan, and ``None`` for aggregates with no build
    recorded.

    ``value_basis`` says what the figures are made from (:data:`VALUE_BASIS`).
    ``timetable_versions`` names the timetables in force over the request's
    days, each listed agency's adding up to the range (:class:`TimetableVersion`),
    for a live scan too; ``None`` until :func:`stamp_build` fills it in.
    ``source_mix`` says, the same way, which ingest source each of those days'
    observations came from (:class:`SourceMix`), counted in days rather than
    observations because a live day holds far fewer of them; it stays ``None``
    on a database not yet migrated to the source counts. ``feed_quality`` is
    each listed agency's completeness and clamp share over the range
    (:class:`FeedQuality`); ``None`` until a router states them, and on a
    definition saved before the stamp carried them.
    """

    preset: str | None
    early_tolerance_sec: int | None
    late_tolerance_sec: int | None
    early_departure_sec: int | None = None
    exclusion_threshold_sec: int = MAX_PLAUSIBLE_DELAY_SEC
    measurement_point: str = MEASUREMENT_POINT
    dedup_rule: str = DEDUP_RULE
    method_version: str = METHOD_VERSION
    source: Literal["aggregates", "live"] = "aggregates"
    built_at: AwareDatetime | None = None
    value_basis: Literal["feed_prediction"] = VALUE_BASIS
    timetable_versions: list[TimetableVersion] | None = None
    source_mix: list[SourceMix] | None = None
    feed_quality: list[FeedQuality] | None = None


# Each agency's builds analyze had recorded, when this process last looked,
# as {agency_id: (table count, sum of build times)}; _UNSEEN before its first look.
_UNSEEN = object()
_seen_builds: object = _UNSEEN

# The builds are named by every row's time, not the newest: analyze dates a
# build when it writes the row and agencies commit concurrently, so a build
# can become visible dated before one already seen. A table's row moves only
# when a run rewrites it (pipeline.analyze._record_fingerprints). With no
# agency named, built_at covers the agencies the board lists, not deleted.
# Not the feed-quality scorecard: no report reads it, and it rewrites its month in progress.
_BUILDS_SQL = """
SELECT s.agency_id,
       count(*) AS builds,
       sum(extract(epoch FROM s.built_at)) AS stamps,
       max(s.built_at) AS built_at,
       a.deleted_at IS NULL AS listed
FROM agg_table_state s
JOIN agencies a USING (agency_id)
WHERE s.table_name <> 'feed_quality_monthly'
GROUP BY s.agency_id, a.deleted_at
"""


_log = logging.getLogger(__name__)

# Each scope agency's per-day counts by ingest source over the range, the scope
# read_schedule_days names. An agency with none recorded in it is one row with
# no date, so it is still named.
_SOURCE_DAYS_SQL = """
SELECT a.agency_id, h.date, h.archive_rows, h.live_rows
FROM agencies a
LEFT JOIN agg_feed_health h ON h.agency_id = a.agency_id AND h.date BETWEEN $2 AND $3
WHERE a.agency_id = $1::int OR ($1::int IS NULL AND a.deleted_at IS NULL)
ORDER BY a.agency_id, h.date
"""


async def stamp_build(
    conn,
    meta: DefinitionMeta,
    agency_id: int | None,
    schedule: ScheduleDays,
    joined: dict[tuple[int, str], str] | None = None,
    *,
    read_feed_quality: bool = True,
) -> DefinitionMeta:
    """*meta* with the timetables in force over *schedule*'s dates, each named
    one with its content's short hash, the source of each of those days'
    observations, and ``built_at``: the newest time analyze rewrote any
    aggregate, all for *agency_id*, or for every agency the board lists when it
    is None. *schedule* is that scope's
    :func:`pipeline.reports.schedule_revision.read_schedule_days`. A live scan
    gets its timetables and sources and no build. Nothing is read from
    ClickHouse.

    One agency's stamp also states its completeness and clamp share over the
    range (:func:`pipeline.reports.network.compute_agency_feed_quality`), read
    from the aggregates beside a live scan too, unless *read_feed_quality* is
    False: a caller that already holds the figures, such as the council report
    from its verdict's board row, states them itself. The board's stamp is
    left without them: the board states each agency's from its own rows
    (``api.routers.network``), which already hold them.

    Beside aggregates, the contents are read from the version map's Postgres
    mirror (:func:`_read_contents`). Beside a live scan they are *joined*: the
    content the join that filed the scan's rows maps each (agency, name) to.
    The mirror can already hold a remap that join, cached per process, has not
    seen yet, or still lag one it has, so it never names a live scan's
    content. A live stamp given no join names none.

    Call it before computing the rows. The compute caches outlive a rebuild,
    so when analyze has rewritten a table since this process last looked, the
    caches that rewrite can have made stale are forgotten first, other agencies'
    rows kept: rows served after this call are then no older than the build it
    names.
    """
    sources = await _read_source_days(conn, agency_id, schedule.from_date, schedule.to_date)
    versions = _timetables(schedule)
    contents = (joined or {}) if meta.source == "live" else await _read_contents(conn, versions)
    update: dict[str, object] = {
        "timetable_versions": _with_contents(versions, contents),
        "source_mix": None if sources is None else _source_mix(sources, schedule.from_date, schedule.to_date),
    }
    if meta.source == "aggregates":
        rows = await conn.fetch(_BUILDS_SQL)
        _note_builds({row[0]: (row[1], row[2]) for row in rows})
        built = [row[3] for row in rows if row[0] == agency_id or (agency_id is None and row[4])]
        update["built_at"] = max(built, default=None)
    if agency_id is not None and read_feed_quality:
        # Imported here: pipeline.reports.network reads the gate, whose module imports this one.
        from pipeline.reports import network

        # Beside aggregates this follows the build check, so a figure cached before
        # a rebuild is forgotten first. A live stamp's figures follow the reader's
        # own cache, which holds them for up to its TTL.
        quality = await network.compute_agency_feed_quality(conn, agency_id, schedule.from_date, schedule.to_date)
        update["feed_quality"] = None if quality is None else [quality]
    return meta.model_copy(update=update)


def _timetables(schedule: ScheduleDays) -> list[TimetableVersion]:
    """Each agency's timetables over *schedule*'s range in the order they first
    ran, each with its days, then the rest of the range as the days none was
    recorded on."""
    range_days = (schedule.to_date - schedule.from_date).days + 1
    versions: list[TimetableVersion] = []
    for agency_id, days in groupby(schedule.rows, key=lambda row: row[0]):
        counts: dict[str, int] = {}
        for _agency, day, name in days:
            if day is not None and schedule.from_date <= day <= schedule.to_date:
                counts[name] = counts.get(name, 0) + 1
        for name, count in counts.items():
            versions.append(TimetableVersion(agency_id=agency_id, name=name, fetched_on=fetch_day(name), days=count))
        recorded = sum(counts.values())
        if recorded < range_days:
            versions.append(
                TimetableVersion(agency_id=agency_id, name=None, fetched_on=None, days=range_days - recorded)
            )
    return versions


# The stamp's enrichments this process has said its database lacks.
_warned_missing: set[str] = set()


async def _fetch_enrichment(conn, what: str, sql: str, *args) -> list | None:
    """*sql*'s rows, or None on a database not yet migrated to *what*. An
    enrichment only adds to the stamp, so its absence leaves that part out
    rather than failing the figures.

    Inside a caller's transaction the read runs in its own savepoint
    (:func:`pipeline.reports.savepoint.savepoint`): a failed statement would
    otherwise abort that transaction, and the caller's next statement with it.
    """
    try:
        async with savepoint(conn):
            return await conn.fetch(sql, *args)
    except (asyncpg.exceptions.UndefinedColumnError, asyncpg.exceptions.UndefinedTableError) as exc:
        if what not in _warned_missing:
            _log.warning("definition stamp: no %s (migrations behind?): %s", what, exc)
            _warned_missing.add(what)
        return None


async def _read_source_days(conn, agency_id: int | None, from_date: date, to_date: date) -> list | None:
    """The :data:`_SOURCE_DAYS_SQL` rows, or None on a database not yet
    migrated to the source counts, whose absence leaves the observations line out."""
    return await _fetch_enrichment(
        conn, "source counts in agg_feed_health", _SOURCE_DAYS_SQL, agency_id, from_date, to_date
    )


# The content the version map's Postgres mirror holds for each bound
# (agency, name) pair, one primary-key probe per timetable named, and whether
# the agency's archive-joined aggregates were last rebuilt since the name was
# mapped to it. A remap clears their states
# (static_archive.clear_archive_joined_states), so until analyze rebuilds them
# they hold figures of the content the name had before, and no build time
# stands after the remap. Both times are compared at the map's millisecond
# precision (static_archive._map_time).
_CONTENTS_SQL = f"""
SELECT n.agency_id, n.static_version_id, m.content_hash,
       (date_trunc('milliseconds', m.archived_at) <= (
           SELECT date_trunc('milliseconds', max(s.built_at)) FROM agg_table_state s
           WHERE s.agency_id = n.agency_id AND s.table_name = ANY($3::text[])
       )) IS TRUE AS built_under
FROM unnest($1::int[], $2::text[]) AS n(agency_id, static_version_id)
JOIN {static_archive.MIRROR} m ON m.agency_id = n.agency_id AND m.static_version_id = n.static_version_id
"""


async def _read_contents(conn, versions: list[TimetableVersion]) -> dict[tuple[int, str], str]:
    """The content the version map maps each named timetable of *versions*
    to, by (agency, name), as its Postgres mirror holds it, for each name the
    agency's aggregates were built under that content (:data:`_CONTENTS_SQL`):
    what a stamp beside aggregates names. Empty on a database not yet migrated
    to the mirror, whose timetables then carry no hash; nothing is read when
    *versions* names none."""
    named = [(version.agency_id, version.name) for version in versions if version.name is not None]
    if not named:
        return {}
    agencies, names = [agency for agency, _ in named], [name for _, name in named]
    tables = static_archive.archive_joined_tables()
    rows = await _fetch_enrichment(conn, static_archive.MIRROR, _CONTENTS_SQL, agencies, names, tables)
    return {} if rows is None else {(row[0], row[1]): row[2] for row in rows if row[3]}


def _with_contents(versions: list[TimetableVersion], contents: dict[tuple[int, str], str]) -> list[TimetableVersion]:
    """*versions*, each named one whose agency and name *contents* maps
    carrying the short form of its content."""
    named = []
    for version in versions:
        content = None if version.name is None else contents.get((version.agency_id, version.name))
        named.append(
            version if content is None else version.model_copy(update={"content_hash": content[:CONTENT_HASH_DIGITS]})
        )
    return named


def _day_source(archive_rows: int | None, live_rows: int | None) -> Literal["archive", "live"] | None:
    """The source most of a day's observations came from, or None when none
    is recorded. A tie reads live, the sparser source, so a day is called
    archive only when archive rows outnumber live ones."""
    if archive_rows is None or live_rows is None or archive_rows + live_rows == 0:
        return None
    return "archive" if archive_rows > live_rows else "live"


def _source_mix(rows: list, from_date: date, to_date: date) -> list[SourceMix]:
    """Each agency's days over [*from_date*, *to_date*] by source, from
    ``(agency_id, date, archive_rows, live_rows)`` rows by agency; every day
    with no source recorded, including those after its newest analyzed day,
    is a none day."""
    range_days = (to_date - from_date).days + 1
    mix: list[SourceMix] = []
    for agency_id, days in groupby(rows, key=lambda row: row[0]):
        sources = [
            _day_source(archive_rows, live_rows)
            for _agency, day, archive_rows, live_rows in days
            if day is not None and from_date <= day <= to_date
        ]
        archive, live = sources.count("archive"), sources.count("live")
        mix.append(
            SourceMix(agency_id=agency_id, archive_days=archive, live_days=live, none_days=range_days - archive - live)
        )
    return mix


def _note_builds(builds: dict[int, tuple]) -> None:
    """Forget the cached compute results a build since this process last looked
    can have made stale: the entries of each agency whose builds moved or
    vanished, and those of no one agency. The first look forgets everything:
    rows cached before it are of unknown builds."""
    global _seen_builds
    seen = _seen_builds
    if seen is _UNSEEN:
        clear_all()
    elif seen != builds:
        assert isinstance(seen, dict)
        evict_for_rebuild(a for a in seen.keys() | builds.keys() if seen.get(a) != builds.get(a))
    _seen_builds = builds


# The reports that show an early-departure rate beside their on-time figures.
_EARLY_RATE_REPORTS = frozenset({"route_table", "council_summary"})


def _preset_name(early_tolerance_sec: int | None, late_tolerance_sec: int) -> str:
    """The preset whose window a resolved on-time window is, or ``custom``.
    Named by the window rather than by how a request spelt it, since a
    window's figures are the same however it was asked for."""
    window = (early_tolerance_sec, late_tolerance_sec)
    return next((name for name, preset in ON_TIME_PRESETS.items() if preset == window), "custom")


def resolve_definition_meta(
    report_type: str,
    early_tolerance_sec: int | None,
    late_tolerance_sec: int | None,
    *,
    live: bool = False,
) -> DefinitionMeta:
    """The definition behind *report_type*'s figures, from the same values
    ``get_report`` passes into the compute functions, so it can never show a
    tolerance different from the one the rows beside it were computed with.

    For the reports with an on-time window, the tolerances are that window,
    already resolved (:func:`pipeline.reports.rankings.on_time_window`): a
    ``None`` early tolerance counts every early departure as on time. Neither
    given is the default window; an early tolerance without a late one is
    refused, since only a request's own spelling could say what it meant.
    For ``worst_5min``, *late_tolerance_sec* is
    its own cutoff, unset for the five-minute column. *live* marks rows from
    a live scan of the observations rather than the aggregates.
    """
    source: Literal["aggregates", "live"] = "live" if live else "aggregates"
    if report_type in ("on_time", "route_table", "council_summary"):
        # One resolution rule for every on-time figure: council_summary pools
        # on_time's window into one whole-agency figure, and route_table's
        # on-time column is on_time's own. route_table's over-5-min share
        # keeps its fixed cutoff, which this metadata does not describe.
        if late_tolerance_sec is None:
            if early_tolerance_sec is not None:
                raise ValueError(f"{report_type}'s definition needs its resolved on-time window")
            early_tolerance_sec, late_tolerance_sec = on_time_window()
        return DefinitionMeta(
            preset=_preset_name(early_tolerance_sec, late_tolerance_sec),
            early_tolerance_sec=early_tolerance_sec,
            late_tolerance_sec=late_tolerance_sec,
            early_departure_sec=(
                early_departure_sec(early_tolerance_sec) if report_type in _EARLY_RATE_REPORTS else None
            ),
            source=source,
        )
    if report_type == "worst_5min":
        if late_tolerance_sec is None:
            # The five-minute column counts the departures past the default
            # window's late edge, so it is named as that window, whose early
            # side does not move the count.
            early = DEFAULT_EARLY_TOLERANCE_SEC
            late = LEGACY_SEVERE_LATE_TOLERANCE_SEC
            return DefinitionMeta(
                preset=_preset_name(early, late), early_tolerance_sec=early, late_tolerance_sec=late, source=source
            )
        return DefinitionMeta(
            preset="custom", early_tolerance_sec=None, late_tolerance_sec=late_tolerance_sec, source=source
        )
    # No on-time/late tolerance concept: the dedup/exclusion/measurement-point
    # defaults still apply, but there is no preset or tolerance to show.
    return DefinitionMeta(preset=None, early_tolerance_sec=None, late_tolerance_sec=None, source=source)


def format_definition_csv_line(meta: DefinitionMeta) -> str:
    """Render *meta* as a single human-readable Japanese line for a CSV
    preamble -- matching the existing CSV export's Japanese-only,
    operator-facing convention (see api/routers/reports.py's
    ``_REPORT_CSV_COLUMNS``/``_LOW_CONFIDENCE_CSV_MARK``, which are not
    locale-switched either).

    Clauses are joined with "; ", not ",", so this single-cell field never
    itself contains a raw comma: ``_csv_response`` writes this ahead of the
    leading BOM character's row, and a comma inside a field that needs
    quoting there would force a quoted cell starting immediately after the
    BOM -- a shape at least the stdlib ``csv`` reader (unlike Excel, which
    special-cases a leading BOM) fails to recognize as quoted, silently
    truncating the cell at the first embedded comma instead. Semicolons
    can't trigger that failure mode since they never need quoting.
    """
    early = "無制限" if meta.early_tolerance_sec is None else f"{meta.early_tolerance_sec}秒"
    late = "対象外" if meta.late_tolerance_sec is None else f"{meta.late_tolerance_sec}秒"
    early_rate = "対象外" if meta.early_departure_sec is None else f"{meta.early_departure_sec}秒を超える早発の割合"
    preset = meta.preset or "対象外"
    preset_name = _PRESET_TEXT.get((preset, "ja"))
    if preset_name is not None:
        preset = f"{preset}（{preset_name}）"
    # Looked up from *meta*'s own fields, not the module constants directly,
    # so an unrecognized value raises KeyError instead of silently rendering
    # stale text -- the same failure mode a caller passing an unexpected
    # preset already gets from _MEASUREMENT_POINT_CSV_TEXT/_DEDUP_RULE_CSV_TEXT
    # only having one entry each today.
    measurement_point = _MEASUREMENT_POINT_CSV_TEXT[meta.measurement_point]
    dedup_rule = _DEDUP_RULE_CSV_TEXT[meta.dedup_rule]
    line = (
        f"定義: プリセット={preset}; 早着許容={early}; 遅延許容={late}; 早発率={early_rate}; "
        f"集計範囲={measurement_point}; 重複排除={dedup_rule}; "
        f"除外基準=|遅延|>{meta.exclusion_threshold_sec}秒を除外; "
        f"値の根拠={_VALUE_BASIS_TEXT[(meta.value_basis, 'ja')]}; "
        f"算出方法の説明={METHODOLOGY_URLS['ja']}; "
        f"算出方法={meta.method_version}; 集計日={_built_day(meta, 'ja')}"
    )
    timetables = _timetable_text(meta, "ja")
    if timetables is not None:
        line = f"{line}; 時刻表={timetables}"
    sources = _source_mix_text(meta, "ja")
    if sources is not None:
        line = f"{line}; 観測データ={sources}"
    quality = _feed_quality_text(meta, "ja", grouped=False)
    return line if quality is None else f"{line}; {_FEED_QUALITY_LABEL['ja']}={quality}"


_LIVE_DAY = {"ja": "対象外（ライブ集計）", "en": "not applicable (live scan)"}
_UNKNOWN_DAY = {"ja": "不明", "en": "unknown"}


def _built_day(meta: DefinitionMeta, locale: str) -> str:
    """The JST day *meta*'s aggregates were built, as YYYY-MM-DD; for a live
    scan, that no build applies; for aggregates with no build recorded, that
    it is unknown."""
    if meta.source == "live":
        return _LIVE_DAY[locale]
    if meta.built_at is None:
        return _UNKNOWN_DAY[locale]
    return jst_date(meta.built_at).isoformat()


def format_method_stamp(meta: DefinitionMeta, locale: str = "ja") -> str:
    """One footnote line naming the method version *meta*'s figures were made
    with and the JST day their aggregates were built, in the words of the CSV
    preamble's 集計日; one of :func:`format_stamp_lines`."""
    if locale == "en":
        return f"Method version: {meta.method_version}, aggregates built: {_built_day(meta, 'en')}"
    return f"算出方法: {meta.method_version}、集計日: {_built_day(meta, 'ja')}"


def _methodology_line(locale: str) -> str:
    """Where the method the stamp names is explained, its link written out
    because paper and a text body keep none."""
    if locale == "en":
        return f"Methodology: {METHODOLOGY_URLS['en']}"
    return f"算出方法の説明: {METHODOLOGY_URLS['ja']}"


def _days(n: int, locale: str) -> str:
    """*n* days as a duration: 日間 in Japanese, so a count after a date never
    reads as a day of the month."""
    if locale == "en":
        return f"{n} day" if n == 1 else f"{n} days"
    return f"{n}日間"


def _timetable_text(meta: DefinitionMeta, locale: str) -> str | None:
    """*meta*'s timetables, each with the days it covers, then the range's
    length, for one agency's document: the board's several agencies are told
    apart only on its screen. None when no router named them."""
    if meta.timetable_versions is None:
        return None
    if not meta.timetable_versions:
        return "none recorded" if locale == "en" else "記録なし"
    parts = [_timetable_part(version, locale) for version in meta.timetable_versions]
    total = _days(sum(version.days for version in meta.timetable_versions), locale)
    if locale == "en":
        return f"{'; '.join(parts)} (of {total})"
    return f"{'、'.join(parts)}（全{total}）"


def _timetable_part(version: TimetableVersion, locale: str) -> str:
    """One timetable and its days, noted with the day its zip was fetched and
    its content's short hash where each is known. The Japanese text joins the
    notes with 、, as the CSV preamble keeps no ASCII comma."""
    days = _days(version.days, locale)
    en = locale == "en"
    if version.name is None:
        return f"none recorded: {days}" if en else f"記録なし {days}"
    notes = []
    if version.fetched_on is not None:
        day = version.fetched_on.isoformat()
        notes.append(f"fetched {day}" if en else f"{day}取得")
    if version.content_hash is not None:
        notes.append(f"content hash {version.content_hash}" if en else f"内容ハッシュ {version.content_hash}")
    if en:
        noted = f" ({', '.join(notes)})" if notes else ""
        return f"{version.name}{noted}: {days}"
    return f"{version.name}（{'、'.join(notes)}）{days}" if notes else f"{version.name} {days}"


# What each source is, said wherever a day of either is counted.
_SOURCE_LABELS = {"ja": ("アーカイブ", "ライブ", "記録なし"), "en": ("archive", "live", "none recorded")}
_SOURCE_NOTE = {
    "ja": "アーカイブの日は収集サーバーがその日に取得した全データ、"
    "ライブの日はアプリがリアルタイムで受け取った一部のデータのみ",
    "en": "Archive days hold every poll the collector made that day; "
    "live days hold only the part the app received in real time",
}


def _source_mix_text(meta: DefinitionMeta, locale: str) -> str | None:
    """The days *meta*'s observations came from each source, the sources with
    none left out, then the range's length and, when either source supplied a
    day, what the two are. Summed over the agencies, for one agency's
    document as :func:`_timetable_text` is. None when no router named them."""
    if meta.source_mix is None:
        return None
    if not meta.source_mix:
        return "none recorded" if locale == "en" else "記録なし"
    counts = (
        sum(mix.archive_days for mix in meta.source_mix),
        sum(mix.live_days for mix in meta.source_mix),
        sum(mix.none_days for mix in meta.source_mix),
    )
    parts = [f"{label} {_days(n, locale)}" for label, n in zip(_SOURCE_LABELS[locale], counts, strict=True) if n]
    total = _days(sum(counts), locale)
    if locale == "en":
        text = f"{', '.join(parts)} (of {total})"
        return f"{text}. {_SOURCE_NOTE['en']}" if counts[0] or counts[1] else text
    text = f"{'、'.join(parts)}（全{total}）"
    return f"{text}。{_SOURCE_NOTE['ja']}" if counts[0] or counts[1] else text


# Worded as the agencies board words the same figures, so a footnote and the board read alike.
_FEED_QUALITY_LABEL = {"ja": "フィード品質（事業者全体）", "en": "Feed quality (whole agency)"}
COVERAGE_MISSING_TEXT: dict[tuple[str, str], str] = {
    # The figures read the aggregates, which a live scan beside them can outrun.
    ("no_data", "ja"): "期間内の集計済みの観測なし",
    ("no_data", "en"): "no analyzed observations in the period",
    ("unreadable", "ja"): "計画を読み込めませんでした",
    ("unreadable", "en"): "the plan could not be read",
    ("unmatched", "ja"): "この配信の便は時刻表の便と照合できません",
    ("unmatched", "en"): "this feed's trips can't be matched to the timetable",
    ("no_plan", "ja"): "期間に時刻表の計画なし",
    ("no_plan", "en"): "no timetable plans this period",
    ("no_plan_observed", "ja"): "観測した日に時刻表の計画なし",
    ("no_plan_observed", "en"): "no timetable plans the days observed",
}


def _feed_quality_text(meta: DefinitionMeta, locale: str, *, grouped: bool = True) -> str | None:
    """Each listed agency's completeness and clamp share, named by its agency
    when there are several; None when no router stated them. *grouped* puts
    thousands separators in the trip counts, which the CSV preamble's single
    cell leaves out, as it keeps no ASCII comma."""
    if not meta.feed_quality:
        return None
    several = len(meta.feed_quality) > 1
    parts = []
    for quality in meta.feed_quality:
        part = _feed_quality_part(quality, locale, grouped)
        if several:
            part = f"agency {quality.agency_id}: {part}" if locale == "en" else f"事業者{quality.agency_id}：{part}"
        parts.append(part)
    return ("; " if locale == "en" else "／").join(parts)


def _feed_quality_part(quality: FeedQuality, locale: str, grouped: bool) -> str:
    # Imported here: the gate's module imports this one.
    from pipeline.reports.comparability import tenth_as_printed

    en = locale == "en"

    def pct(value: float) -> str:
        return f"{tenth_as_printed(value):.1f}"

    def count(n: int) -> str:
        return f"{n:,}" if grouped else str(n)

    if quality.coverage_pct is None:
        why = COVERAGE_MISSING_TEXT[(quality.coverage_missing or "no_data", locale)]
        coverage = f"observation coverage — ({why})" if en else f"観測カバー率 —（{why}）"
    elif quality.coverage_low:
        floor = f"{quality.min_coverage_pct:g}"
        coverage = (
            f"observation coverage {pct(quality.coverage_pct)}% (below {floor}%)"
            if en
            else f"観測カバー率 {pct(quality.coverage_pct)}%（{floor}%未満）"
        )
    elif quality.observed_trips is not None and quality.coverage_planned_trips is not None:
        observed, planned = count(quality.observed_trips), count(quality.coverage_planned_trips)
        coverage = (
            f"observation coverage {pct(quality.coverage_pct)}% ({observed} of {planned} planned trips)"
            if en
            else f"観測カバー率 {pct(quality.coverage_pct)}%（計画{planned}便中{observed}便）"
        )
    else:
        coverage = "observation coverage —" if en else "観測カバー率 —"
    if quality.clamp_pct is None:
        none = COVERAGE_MISSING_TEXT[("no_data", locale)]
        clamp = f"implausible readings — ({none})" if en else f"異常値 —（{none}）"
    elif quality.clamp_high:
        ceiling = f"{quality.max_clamp_pct:g}"
        clamp = (
            f"implausible readings {pct(quality.clamp_pct)}% (above {ceiling}%)"
            if en
            else f"異常値 {pct(quality.clamp_pct)}%（{ceiling}%超）"
        )
    else:
        clamp = f"implausible readings {pct(quality.clamp_pct)}%" if en else f"異常値 {pct(quality.clamp_pct)}%"
    return f"{coverage}, {clamp}" if en else f"{coverage}、{clamp}"


def format_stamp_lines(meta: DefinitionMeta, locale: str = "ja") -> list[str]:
    """The footnote lines that say where *meta*'s figures came from: what they
    are made from, where the method is published, the method and build day
    (:func:`format_method_stamp`), and the timetables in force, the source
    of each day's observations and the period's completeness and clamp share
    when a router named them.
    :func:`format_definition_footnotes` ends with them, and the delay record's
    text is footnoted with them."""
    locale = "en" if locale == "en" else "ja"
    basis = _VALUE_BASIS_TEXT[(meta.value_basis, locale)]
    lines = [
        f"Value basis: {basis}" if locale == "en" else f"値の根拠: {basis}",
        _methodology_line(locale),
        format_method_stamp(meta, locale),
    ]
    timetables = _timetable_text(meta, locale)
    if timetables is not None:
        lines.append(f"Timetable: {timetables}" if locale == "en" else f"時刻表: {timetables}")
    sources = _source_mix_text(meta, locale)
    if sources is not None:
        lines.append(f"Observations: {sources}" if locale == "en" else f"観測データ: {sources}")
    quality = _feed_quality_text(meta, locale)
    if quality is not None:
        lines.append(f"{_FEED_QUALITY_LABEL[locale]}: {quality}")
    return lines


def format_definition_footnotes(meta: DefinitionMeta, locale: str = "ja") -> list[str]:
    """Render *meta* as a list of locale-aware footnote lines for a report
    template (a monthly/annual council-audience report, unlike
    :func:`format_definition_csv_line`'s single Japanese-only CSV preamble
    cell). Every value is read from *meta* itself, never a fixed string, so a
    report rendered with a non-default tolerance/definition shows those exact
    values here too -- the same guarantee the CSV preamble already gives.

    The on-time rule is stated in a reader's words (:func:`_on_time_rule`),
    not by its preset identifier, which is for comparing exports.
    It is omitted when *meta* has no late tolerance, a report type with no
    on-time concept (see :class:`DefinitionMeta`), unlike
    :func:`format_definition_csv_line`, whose single preamble cell keeps a
    stable shape for every report type.

    Raises ``KeyError`` for an unrecognized ``measurement_point``/
    ``dedup_rule`` -- same failure mode as ``format_definition_csv_line``;
    see that function's own docstring for why a loud failure beats silently
    rendering stale text.
    """
    if locale not in ("ja", "en"):
        locale = "ja"
    measurement_point = _MEASUREMENT_POINT_TEXT[(meta.measurement_point, locale)]
    dedup_rule = _DEDUP_RULE_TEXT[(meta.dedup_rule, locale)]

    lines: list[str] = []
    if meta.late_tolerance_sec is not None:
        lines.append(_on_time_rule(meta.late_tolerance_sec, meta.early_tolerance_sec, meta.preset, locale))
    if meta.early_departure_sec is not None:
        lines.append(_early_departure_rule(meta.early_departure_sec, locale))

    if locale == "en":
        lines.append(f"Measurement scope: {measurement_point}")
        lines.append(f"Deduplication rule: {dedup_rule}")
        lines.append(f"Exclusion rule: |delay| > {meta.exclusion_threshold_sec}s excluded as implausible")
    else:
        lines.append(f"集計範囲: {measurement_point}")
        lines.append(f"重複排除: {dedup_rule}")
        lines.append(f"除外基準: |遅延|>{meta.exclusion_threshold_sec}秒を除外")
    lines.extend(format_stamp_lines(meta, locale))
    return lines


def duration_text(sec: int, locale: str) -> str:
    """*sec* as whole minutes and seconds, the way the screens write a
    tolerance: 60 -> "1分" / "1 min", 90 -> "1分30秒" / "1 min 30 s"."""
    minutes, seconds = divmod(sec, 60)
    if locale == "en":
        parts = ([f"{minutes} min"] if minutes else []) + ([f"{seconds} s"] if seconds or not minutes else [])
        return " ".join(parts)
    return (f"{minutes}分" if minutes else "") + (f"{seconds}秒" if seconds or not minutes else "")


def _on_time_rule(late_sec: int, early_sec: int | None, preset: str | None, locale: str) -> str:
    """What counts as on time: up to *late_sec* late and, with no early
    tolerance, any early departure, which is said outright because a reader
    otherwise assumes a window on both sides. A named preset is called by its
    reader's name (:data:`_PRESET_TEXT`), so a figure compared with a
    published one says which convention it follows."""
    late = duration_text(late_sec, locale)
    early = None if early_sec is None else duration_text(early_sec, locale)
    name = _PRESET_TEXT.get((preset or "", locale))
    if locale == "en":
        lead = "On-time definition" if name is None else f"On-time definition ({name})"
        if early is None:
            return f"{lead}: no more than {late} late (early departures count as on time)"
        return f"{lead}: no more than {late} late and no more than {early} early"
    lead = "定時の定義" if name is None else f"定時の定義（{name}）"
    if early is None:
        return f"{lead}：定刻から{late}以内の遅れ（早発も定時として数えます）"
    return f"{lead}：定刻から{late}以内の遅れと、{early}以内の早発"


def _early_departure_rule(early_sec: int, locale: str) -> str:
    """What the early-departure rate counts: departures more than *early_sec*
    early."""
    early = duration_text(early_sec, locale)
    if locale == "en":
        return f"Early-departure rate: the share of departures more than {early} early"
    return f"早発率：定刻より{early}を超えて早く発車した割合"
