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

# Every report/comparison view reads through pipeline.db.build_dedup_ch_sql,
# which resolves duplicate observations for the same stop event (same route,
# service_type, scheduled_time, trip_id, JST calendar day, stop_sequence) by
# keeping the one with the latest (captured_at, file_name) -- see that
# function's own docstring for why "latest wins" replaced a prior
# "MAX(dep_delay)" behavior. Named here as a stable identifier (not directly
# imported from pipeline.db, which has no such constant of its own) so a
# JSON response can carry it as data and let the frontend render translated
# text, while the CSV export below renders it inline.
DEDUP_RULE = "latest_observation_per_stop_event"

# Every aggregate a report or comparison view reads pools across ALL stops
# (not just termini) and ALL observations in the requested range -- there is
# no separate "measurement point" knob anywhere in the pipeline today, so
# this is a fixed descriptor rather than something resolved per request.
MEASUREMENT_POINT = "all_stops_all_observations"


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
    if report_type == "on_time":
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
    return (
        f"定義: プリセット={preset}; 早着許容={early}; 遅延許容={late}; "
        f"集計範囲=全停留所・全観測; 重複排除=停留所イベントごとに最新観測を採用; "
        f"除外基準=|遅延|>{meta.exclusion_threshold_sec}秒を除外"
    )
