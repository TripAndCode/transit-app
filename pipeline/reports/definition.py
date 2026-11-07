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

# Stable identifiers (not resolved per request, unlike the tolerance fields
# below) so a caller can render/translate them instead of a fixed string
# baked into this module. ``pipeline.db.build_dedup_ch_sql`` implements the
# dedup rule itself (latest observation per stop event wins); no separate
# "measurement point" knob exists elsewhere in the pipeline today.
DEDUP_RULE = "latest_observation_per_stop_event"
MEASUREMENT_POINT = "all_stops_all_observations"

# Locale-aware operator-facing text for MEASUREMENT_POINT/DEDUP_RULE, keyed by
# (value, locale) tuples rather than re-typed string literals -- a dict keyed
# by a bare name is resolved against that name's *current* value, so this
# can't silently fall out of sync with either constant the way independently
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

# format_definition_csv_line's own Japanese-only lookups: a plain
# {value: text} dict, so an unrecognized value raises KeyError, sourced from
# the locale-aware dicts above's "ja" slice instead of repeating either string
# a second time.
_MEASUREMENT_POINT_CSV_TEXT: dict[str, str] = {MEASUREMENT_POINT: _MEASUREMENT_POINT_TEXT[(MEASUREMENT_POINT, "ja")]}
_DEDUP_RULE_CSV_TEXT: dict[str, str] = {DEDUP_RULE: _DEDUP_RULE_TEXT[(DEDUP_RULE, "ja")]}


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

    ``method_version`` names the analysis method the figures were made with
    (:data:`pipeline.agg_registry.METHOD_VERSION`). ``source`` says whether
    they came from the aggregates or from a live scan of the observations
    (:func:`pipeline.reports.rankings.report_reads_aggregates`). ``built_at``
    is when analyze last rewrote the aggregates behind them
    (:func:`stamp_build`), which a router with a connection fills in: always
    ``None`` for a live scan, and ``None`` for aggregates with no build
    recorded.
    """

    preset: str | None
    early_tolerance_sec: int | None
    late_tolerance_sec: int | None
    exclusion_threshold_sec: int = MAX_PLAUSIBLE_DELAY_SEC
    measurement_point: str = MEASUREMENT_POINT
    dedup_rule: str = DEDUP_RULE
    method_version: str = METHOD_VERSION
    source: Literal["aggregates", "live"] = "aggregates"
    built_at: AwareDatetime | None = None


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


async def stamp_build(conn, meta: DefinitionMeta, agency_id: int | None) -> DefinitionMeta:
    """*meta* with ``built_at``: the newest time analyze rewrote any
    aggregate of *agency_id*, or of every agency the board lists when it is
    None. A live definition is returned as it is.

    Call it before computing the rows. The compute caches outlive a rebuild,
    so when analyze has rewritten any table since this process last looked, every
    cache is forgotten first: rows served after this call are then no older
    than the build it names.
    """
    if meta.source == "live":
        return meta
    builds, stamps, built_at = await conn.fetchrow(_BUILDS_SQL, agency_id)
    _note_builds((builds, stamps))
    return meta.model_copy(update={"built_at": built_at})


def _note_builds(builds: object) -> None:
    """Forget every cached compute result when *builds* are not the ones this
    process last saw. The first look forgets too: rows cached before it are of
    unknown builds."""
    global _seen_builds
    if builds != _seen_builds:
        clear_all()
        _seen_builds = builds


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
        is_legacy = early_tolerance_sec is None and late_tolerance_sec is None
        resolved_late = LEGACY_ON_TIME_LATE_TOLERANCE_SEC if late_tolerance_sec is None else late_tolerance_sec
        return DefinitionMeta(
            preset=LEGACY_PRESET_NAME if is_legacy else "custom",
            early_tolerance_sec=early_tolerance_sec,
            late_tolerance_sec=resolved_late,
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
    preset = meta.preset or "対象外"
    # Looked up from *meta*'s own fields, not the module constants directly,
    # so an unrecognized value raises KeyError instead of silently rendering
    # stale text -- the same failure mode a caller passing an unexpected
    # preset already gets from _MEASUREMENT_POINT_CSV_TEXT/_DEDUP_RULE_CSV_TEXT
    # only having one entry each today.
    measurement_point = _MEASUREMENT_POINT_CSV_TEXT[meta.measurement_point]
    dedup_rule = _DEDUP_RULE_CSV_TEXT[meta.dedup_rule]
    return (
        f"定義: プリセット={preset}; 早着許容={early}; 遅延許容={late}; "
        f"集計範囲={measurement_point}; 重複排除={dedup_rule}; "
        f"除外基準=|遅延|>{meta.exclusion_threshold_sec}秒を除外; "
        f"算出方法={meta.method_version}; 集計日={_built_day(meta, 'ja')}"
    )


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
    preamble's 集計日. :func:`format_definition_footnotes` ends with it, and the
    delay record's text is footnoted with it, so a document says which method
    produced it."""
    if locale == "en":
        return f"Method version: {meta.method_version}, aggregates built: {_built_day(meta, 'en')}"
    return f"算出方法: {meta.method_version}、集計日: {_built_day(meta, 'ja')}"


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

    if locale == "en":
        lines.append(f"Measurement scope: {measurement_point}")
        lines.append(f"Deduplication rule: {dedup_rule}")
        lines.append(f"Exclusion rule: |delay| > {meta.exclusion_threshold_sec}s excluded as implausible")
    else:
        lines.append(f"集計範囲: {measurement_point}")
        lines.append(f"重複排除: {dedup_rule}")
        lines.append(f"除外基準: |遅延|>{meta.exclusion_threshold_sec}秒を除外")
    lines.append(format_method_stamp(meta, locale))
    return lines


def _duration_text(sec: int, locale: str) -> str:
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
    late = _duration_text(late_sec, locale)
    early = None if early_sec is None else _duration_text(early_sec, locale)
    if locale == "en":
        if early is None:
            return f"On-time definition: no more than {late} late (early departures count as on time)"
        return f"On-time definition: no more than {late} late and no more than {early} early"
    if early is None:
        return f"定時の定義：定刻から{late}以内の遅れ（早発も定時として数えます）"
    return f"定時の定義：定刻から{late}以内の遅れと、{early}以内の早発"
