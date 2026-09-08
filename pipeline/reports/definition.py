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

from pydantic import BaseModel

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

# format_definition_csv_line's own Japanese-only lookups -- unchanged shape (a
# plain {value: text} dict, so an unrecognized value still raises KeyError
# exactly as before), now sourced from the locale-aware dicts above's "ja"
# slice instead of repeating either string a second time.
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
    """

    preset: str | None
    early_tolerance_sec: int | None
    late_tolerance_sec: int | None
    exclusion_threshold_sec: int = MAX_PLAUSIBLE_DELAY_SEC
    measurement_point: str = MEASUREMENT_POINT
    dedup_rule: str = DEDUP_RULE


def resolve_definition_meta(
    report_type: str,
    early_tolerance_sec: int | None,
    late_tolerance_sec: int | None,
) -> DefinitionMeta:
    """Resolve the early/late tolerance actually applied for *report_type*,
    from the same (already-validated) query params ``get_report`` passes
    into :func:`pipeline.reports.rankings.compute_on_time`/
    :func:`~pipeline.reports.rankings.compute_worst_5min`. Mirrors those two
    functions' own None-default resolution exactly, so this metadata block
    can never show a tolerance different from the one actually used to
    compute the rows next to it.
    """
    if report_type in ("on_time", "council_summary"):
        # council_summary (the monthly/annual report template) reuses the
        # exact same on-time tolerance semantics as on_time -- it pools
        # the same underlying window into one whole-agency figure instead of
        # a per-route breakdown, so the two share this branch rather than a
        # second, independently-typed copy of the same resolution rule.
        is_legacy = early_tolerance_sec is None and late_tolerance_sec is None
        resolved_late = LEGACY_ON_TIME_LATE_TOLERANCE_SEC if late_tolerance_sec is None else late_tolerance_sec
        return DefinitionMeta(
            preset=LEGACY_PRESET_NAME if is_legacy else "custom",
            early_tolerance_sec=early_tolerance_sec,
            late_tolerance_sec=resolved_late,
        )
    if report_type == "worst_5min":
        is_legacy = late_tolerance_sec is None
        resolved_late = LEGACY_SEVERE_LATE_TOLERANCE_SEC if late_tolerance_sec is None else late_tolerance_sec
        return DefinitionMeta(
            preset=LEGACY_PRESET_NAME if is_legacy else "custom",
            early_tolerance_sec=None,
            late_tolerance_sec=resolved_late,
        )
    # ranking/ranking_best/trend/compare_ranking/dow_weekend/dow_weekday: no
    # on-time/late tolerance concept -- dedup/exclusion/measurement-point
    # still apply (defaulted above), but there's no preset/tolerance to show.
    return DefinitionMeta(preset=None, early_tolerance_sec=None, late_tolerance_sec=None)


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
        f"除外基準=|遅延|>{meta.exclusion_threshold_sec}秒を除外"
    )


def format_definition_footnotes(meta: DefinitionMeta, locale: str = "ja") -> list[str]:
    """Render *meta* as a list of locale-aware footnote lines for a report
    template (a monthly/annual council-audience report, unlike
    :func:`format_definition_csv_line`'s single Japanese-only CSV preamble
    cell). Every value is read from *meta* itself, never a fixed string, so a
    report rendered with a non-default tolerance/definition shows those exact
    values here too -- the same guarantee the CSV preamble already gives.

    The tolerance/preset line is omitted entirely when *meta* has no
    on-time/late tolerance concept at all (``preset is None`` and both
    tolerance fields are ``None`` -- see :class:`DefinitionMeta`'s own
    docstring), matching :func:`format_definition_csv_line`'s own choice to
    still render a "対象外" (n/a) preset there rather than nothing: a CSV's
    single preamble cell needs a stable column count regardless of report
    type, while a footnote list has no such constraint and can just leave the
    line out.

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
    has_tolerance_concept = (
        meta.preset is not None or meta.early_tolerance_sec is not None or meta.late_tolerance_sec is not None
    )
    if has_tolerance_concept:
        if locale == "en":
            early = "unbounded" if meta.early_tolerance_sec is None else f"{meta.early_tolerance_sec}s"
            late = "n/a" if meta.late_tolerance_sec is None else f"{meta.late_tolerance_sec}s"
            preset = meta.preset or "n/a"
            lines.append(f"Definition: preset={preset}; early tolerance={early}; late tolerance={late}")
        else:
            early = "無制限" if meta.early_tolerance_sec is None else f"{meta.early_tolerance_sec}秒"
            late = "対象外" if meta.late_tolerance_sec is None else f"{meta.late_tolerance_sec}秒"
            preset = meta.preset or "対象外"
            lines.append(f"定義: プリセット={preset}; 早着許容={early}; 遅延許容={late}")

    if locale == "en":
        lines.append(f"Measurement scope: {measurement_point}")
        lines.append(f"Deduplication rule: {dedup_rule}")
        lines.append(f"Exclusion rule: |delay| > {meta.exclusion_threshold_sec}s excluded as implausible")
    else:
        lines.append(f"集計範囲: {measurement_point}")
        lines.append(f"重複排除: {dedup_rule}")
        lines.append(f"除外基準: |遅延|>{meta.exclusion_threshold_sec}秒を除外")
    return lines
