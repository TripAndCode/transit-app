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

from datetime import date
from itertools import groupby
from typing import Literal

from pydantic import AwareDatetime, BaseModel

from pipeline.agg_registry import METHOD_VERSION
from pipeline.cache import clear_all
from pipeline.clickhouse import jst_date
from pipeline.db import MAX_PLAUSIBLE_DELAY_SEC
from pipeline.histogram import (
    LEGACY_ON_TIME_LATE_TOLERANCE_SEC,
    LEGACY_PRESET_NAME,
    LEGACY_SEVERE_LATE_TOLERANCE_SEC,
)
from pipeline.reports.rankings import ON_TIME_PRESETS, early_departure_sec
from pipeline.reports.schedule_revision import ScheduleDays
from pipeline.static_archive import fetch_day

# Stable identifiers (not resolved per request, unlike the tolerance fields
# below) so a caller can render/translate them instead of a fixed string
# baked into this module. ``pipeline.db.build_dedup_ch_sql`` implements the
# dedup rule itself (latest observation per stop event wins); no separate
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
    (DEDUP_RULE, "ja"): "停留所イベントごとに最新観測を採用",
    (DEDUP_RULE, "en"): "latest observation per stop event",
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


class TimetableVersion(BaseModel):
    """A timetable an agency's observations were ingested against, and on how
    many of a request's days it was the one most of them carried
    (agg_schedule_revision_daily). ``name`` is the loaded zip's version name,
    ``fetched_on`` the day that name says it was fetched; ``name`` is None for
    the days with no timetable recorded: none analyzed yet, or an ingest that
    stamps no version."""

    agency_id: int
    name: str | None
    fetched_on: date | None
    days: int


class SourceMix(BaseModel):
    """How many of a request's days an agency's observations came mostly from
    the collector's archive, which holds every poll it made, or from the live
    path, which holds only what the app received in real time and so is the
    sparser of the two. The rest are the days with no source recorded: no
    observations, a day not analyzed yet, or one analyzed before sources were
    counted. The three add up to the range (:func:`_day_source`)."""

    agency_id: int
    archive_days: int
    live_days: int
    none_days: int


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
    observations because a live day holds far fewer of them.
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


# The builds analyze had recorded, for every agency, when this process last
# looked; _UNSEEN before its first look.
_UNSEEN = object()
_seen_builds: object = _UNSEEN

# The builds are named by every row's time, not the newest: analyze dates a
# build when it writes the row and agencies commit concurrently, so a build
# can become visible dated before one already seen. A table's row moves only
# when a run rewrites it (pipeline.analyze._record_fingerprints). With no
# agency named, built_at covers the agencies the board lists, not deleted.
_BUILDS_SQL = """
SELECT count(*) AS builds,
       sum(extract(epoch FROM s.built_at)) AS stamps,
       max(s.built_at) FILTER (WHERE s.agency_id = $1::int OR ($1::int IS NULL AND a.deleted_at IS NULL)) AS built_at
FROM agg_table_state s
JOIN agencies a USING (agency_id)
"""


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


async def stamp_build(conn, meta: DefinitionMeta, agency_id: int | None, schedule: ScheduleDays) -> DefinitionMeta:
    """*meta* with the timetables in force over *schedule*'s dates, the source
    of each of those days' observations, and ``built_at``: the newest time
    analyze rewrote any aggregate, all for *agency_id*, or for every agency
    the board lists when it is None. *schedule* is that scope's
    :func:`pipeline.reports.schedule_revision.read_schedule_days`. A live scan
    gets its timetables and sources and no build.

    Call it before computing the rows. The compute caches outlive a rebuild,
    so when analyze has rewritten any table since this process last looked, every
    cache is forgotten first: rows served after this call are then no older
    than the build it names.
    """
    sources = await conn.fetch(_SOURCE_DAYS_SQL, agency_id, schedule.from_date, schedule.to_date)
    update: dict[str, object] = {
        "timetable_versions": _timetables(schedule),
        "source_mix": _source_mix(sources, schedule.from_date, schedule.to_date),
    }
    if meta.source == "aggregates":
        builds, stamps, built_at = await conn.fetchrow(_BUILDS_SQL, agency_id)
        _note_builds((builds, stamps))
        update["built_at"] = built_at
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


def _note_builds(builds: object) -> None:
    """Forget every cached compute result when *builds* are not the ones this
    process last saw. The first look forgets too: rows cached before it are of
    unknown builds."""
    global _seen_builds
    if builds != _seen_builds:
        clear_all()
        _seen_builds = builds


# The reports that show an early-departure rate beside their on-time figures.
_EARLY_RATE_REPORTS = frozenset({"route_table", "council_summary"})


def _preset_name(early_tolerance_sec: int | None, late_tolerance_sec: int | None) -> str:
    """The preset whose window the requested tolerances are, compared before
    any default fills in, so a request that spells out its tolerances is never
    named the legacy default, which spells out none."""
    requested = (early_tolerance_sec, late_tolerance_sec)
    return next((name for name, window in ON_TIME_PRESETS.items() if window == requested), "custom")


def resolve_definition_meta(
    report_type: str,
    early_tolerance_sec: int | None,
    late_tolerance_sec: int | None,
    *,
    live: bool = False,
) -> DefinitionMeta:
    """Resolve the early/late tolerance actually applied for *report_type*,
    from the same (already-validated) query params ``get_report`` passes
    into :func:`pipeline.reports.rankings.compute_on_time`/
    :func:`~pipeline.reports.rankings.compute_worst_5min`. Mirrors those two
    functions' own None-default resolution exactly, so this metadata block
    can never show a tolerance different from the one actually used to
    compute the rows next to it. *live* marks rows from a live scan of the
    observations rather than the aggregates.
    """
    source: Literal["aggregates", "live"] = "live" if live else "aggregates"
    if report_type in ("on_time", "route_table", "council_summary"):
        # One resolution rule for every on-time figure: council_summary pools
        # on_time's window into one whole-agency figure, and route_table's
        # on-time column is on_time's own. route_table's over-5-min share
        # keeps its fixed cutoff, which this metadata does not describe.
        resolved_late = LEGACY_ON_TIME_LATE_TOLERANCE_SEC if late_tolerance_sec is None else late_tolerance_sec
        return DefinitionMeta(
            preset=_preset_name(early_tolerance_sec, late_tolerance_sec),
            early_tolerance_sec=early_tolerance_sec,
            late_tolerance_sec=resolved_late,
            early_departure_sec=(
                early_departure_sec(early_tolerance_sec) if report_type in _EARLY_RATE_REPORTS else None
            ),
            source=source,
        )
    if report_type == "worst_5min":
        is_legacy = late_tolerance_sec is None
        resolved_late = LEGACY_SEVERE_LATE_TOLERANCE_SEC if late_tolerance_sec is None else late_tolerance_sec
        return DefinitionMeta(
            preset=LEGACY_PRESET_NAME if is_legacy else "custom",
            early_tolerance_sec=None,
            late_tolerance_sec=resolved_late,
            source=source,
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
        f"算出方法={meta.method_version}; 集計日={_built_day(meta, 'ja')}"
    )
    timetables = _timetable_text(meta, "ja")
    if timetables is not None:
        line = f"{line}; 時刻表={timetables}"
    sources = _source_mix_text(meta, "ja")
    return line if sources is None else f"{line}; 観測データ={sources}"


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
    days = _days(version.days, locale)
    if version.name is None:
        return f"none recorded: {days}" if locale == "en" else f"記録なし {days}"
    if locale == "en":
        fetched = "" if version.fetched_on is None else f" (fetched {version.fetched_on.isoformat()})"
        return f"{version.name}{fetched}: {days}"
    if version.fetched_on is None:
        return f"{version.name} {days}"
    return f"{version.name}（{version.fetched_on.isoformat()}取得）{days}"


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


def format_stamp_lines(meta: DefinitionMeta, locale: str = "ja") -> list[str]:
    """The footnote lines that say where *meta*'s figures came from: what they
    are made from, the method and build day (:func:`format_method_stamp`), and the
    timetables in force and the source of each day's observations when a
    router named them.
    :func:`format_definition_footnotes` ends with them, and the delay record's
    text is footnoted with them."""
    locale = "en" if locale == "en" else "ja"
    basis = _VALUE_BASIS_TEXT[(meta.value_basis, locale)]
    lines = [f"Value basis: {basis}" if locale == "en" else f"値の根拠: {basis}", format_method_stamp(meta, locale)]
    timetables = _timetable_text(meta, locale)
    if timetables is not None:
        lines.append(f"Timetable: {timetables}" if locale == "en" else f"時刻表: {timetables}")
    sources = _source_mix_text(meta, locale)
    if sources is not None:
        lines.append(f"Observations: {sources}" if locale == "en" else f"観測データ: {sources}")
    return lines


def format_definition_footnotes(meta: DefinitionMeta, locale: str = "ja") -> list[str]:
    """Render *meta* as a list of locale-aware footnote lines for a report
    template (a monthly/annual council-audience report, unlike
    :func:`format_definition_csv_line`'s single Japanese-only CSV preamble
    cell). Every value is read from *meta* itself, never a fixed string, so a
    report rendered with a non-default tolerance/definition shows those exact
    values here too -- the same guarantee the CSV preamble already gives.

    The on-time rule is stated in a reader's words (:func:`_on_time_rule`),
    not by its preset name, which is an identifier for comparing exports.
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
        lines.append(_on_time_rule(meta.late_tolerance_sec, meta.early_tolerance_sec, locale))
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


def _on_time_rule(late_sec: int, early_sec: int | None, locale: str) -> str:
    """What counts as on time: up to *late_sec* late and, with no early
    tolerance, any early departure, which is said outright because a reader
    otherwise assumes a window on both sides."""
    late = duration_text(late_sec, locale)
    early = None if early_sec is None else duration_text(early_sec, locale)
    if locale == "en":
        if early is None:
            return f"On-time definition: no more than {late} late (early departures count as on time)"
        return f"On-time definition: no more than {late} late and no more than {early} early"
    if early is None:
        return f"定時の定義：定刻から{late}以内の遅れ（早発も定時として数えます）"
    return f"定時の定義：定刻から{late}以内の遅れと、{early}以内の早発"


def _early_departure_rule(early_sec: int, locale: str) -> str:
    """What the early-departure rate counts: departures more than *early_sec*
    early."""
    early = duration_text(early_sec, locale)
    if locale == "en":
        return f"Early-departure rate: the share of departures more than {early} early"
    return f"早発率：定刻より{early}を超えて早く発車した割合"
