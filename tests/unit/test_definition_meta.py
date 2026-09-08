"""Pure-logic tests for pipeline.reports.definition -- the shared "which
tolerance/dedup/exclusion rule did this report actually use" metadata block.
Mirrors compute_on_time/compute_worst_5min's own None-default resolution
(pipeline/reports/rankings.py) so the values these tests assert can never
silently drift from what a report actually computes.
"""

import pytest

from pipeline.db import MAX_PLAUSIBLE_DELAY_SEC
from pipeline.histogram import (
    LEGACY_ON_TIME_LATE_TOLERANCE_SEC,
    LEGACY_PRESET_NAME,
    LEGACY_SEVERE_LATE_TOLERANCE_SEC,
)
from pipeline.reports.definition import (
    DEDUP_RULE,
    MEASUREMENT_POINT,
    format_definition_csv_line,
    format_definition_footnotes,
    resolve_definition_meta,
)


def test_on_time_no_params_resolves_to_legacy_preset():
    meta = resolve_definition_meta("on_time", None, None)
    assert meta.preset == LEGACY_PRESET_NAME == "legacy_60s"
    assert meta.early_tolerance_sec is None
    assert meta.late_tolerance_sec == LEGACY_ON_TIME_LATE_TOLERANCE_SEC == 60


def test_on_time_explicit_tolerance_resolves_to_custom_preset_with_exact_values():
    meta = resolve_definition_meta("on_time", 30, 120)
    assert meta.preset == "custom"
    assert meta.early_tolerance_sec == 30
    assert meta.late_tolerance_sec == 120


def test_on_time_explicit_late_only_resolves_to_custom_with_unbounded_early():
    # Passing EITHER param opts out of the legacy preset, even if the other
    # is left unset -- matches compute_on_time's own "any explicit tolerance
    # switches to the histogram path" contract.
    meta = resolve_definition_meta("on_time", None, 90)
    assert meta.preset == "custom"
    assert meta.early_tolerance_sec is None
    assert meta.late_tolerance_sec == 90


def test_on_time_preset_numerically_equal_to_legacy_is_still_custom():
    # An explicit late_tolerance_sec=60 (same number as the legacy default)
    # must still resolve to "custom", not silently collapse back to
    # "legacy_60s" -- the two are different code paths upstream (exact
    # column vs. histogram estimate) and the metadata block must say so.
    meta = resolve_definition_meta("on_time", None, 60)
    assert meta.preset == "custom"
    assert meta.late_tolerance_sec == 60


def test_worst_5min_no_params_resolves_to_legacy_preset():
    meta = resolve_definition_meta("worst_5min", None, None)
    assert meta.preset == LEGACY_PRESET_NAME
    assert meta.early_tolerance_sec is None
    assert meta.late_tolerance_sec == LEGACY_SEVERE_LATE_TOLERANCE_SEC == 300


def test_worst_5min_explicit_tolerance_resolves_to_custom():
    meta = resolve_definition_meta("worst_5min", None, 180)
    assert meta.preset == "custom"
    assert meta.late_tolerance_sec == 180


def test_report_types_without_a_tolerance_concept_have_no_preset():
    for report_type in ("ranking", "ranking_best", "trend", "compare_ranking", "dow_weekend", "dow_weekday"):
        meta = resolve_definition_meta(report_type, None, None)
        assert meta.preset is None
        assert meta.early_tolerance_sec is None
        assert meta.late_tolerance_sec is None


def test_dedup_and_exclusion_and_measurement_point_are_always_present():
    # These three fields don't depend on report_type or tolerance -- every
    # aggregate this app serves shares the one dedup builder and exclusion
    # ceiling (pipeline.db.build_dedup_ch_sql).
    for report_type, early, late in (("on_time", None, None), ("ranking", None, None), ("worst_5min", None, 999)):
        meta = resolve_definition_meta(report_type, early, late)
        assert meta.exclusion_threshold_sec == MAX_PLAUSIBLE_DELAY_SEC == 7200
        assert meta.measurement_point == MEASUREMENT_POINT
        assert meta.dedup_rule == DEDUP_RULE


def test_csv_line_reflects_custom_tolerance_not_legacy_defaults():
    """A CSV/report exported with non-default tolerances must show those
    exact values, not the legacy_60s defaults."""
    meta = resolve_definition_meta("on_time", 30, 120)
    line = format_definition_csv_line(meta)
    assert "30秒" in line
    assert "120秒" in line
    assert "custom" in line
    assert "legacy_60s" not in line
    assert "60秒" not in line  # the legacy late-tolerance default must not leak in
    assert str(MAX_PLAUSIBLE_DELAY_SEC) in line


def test_csv_line_default_shows_legacy_preset_and_unbounded_early():
    meta = resolve_definition_meta("on_time", None, None)
    line = format_definition_csv_line(meta)
    assert "legacy_60s" in line
    assert "無制限" in line  # unbounded early tolerance
    assert "60秒" in line


def test_csv_line_measurement_point_text_is_read_from_the_field_not_hardcoded():
    """format_definition_csv_line must derive its measurement-point text from
    meta.measurement_point -- not print a fixed string regardless of the
    field's value. An unrecognized identifier must fail loudly (KeyError)
    rather than silently rendering stale text, proving the lookup is real:
    a hardcoded implementation would ignore the bogus value and succeed."""
    meta = resolve_definition_meta("on_time", None, None).model_copy(
        update={"measurement_point": "some_other_measurement_point"}
    )
    with pytest.raises(KeyError):
        format_definition_csv_line(meta)


def test_csv_line_dedup_rule_text_is_read_from_the_field_not_hardcoded():
    """Same guarantee as the measurement-point test above, for dedup_rule."""
    meta = resolve_definition_meta("on_time", None, None).model_copy(update={"dedup_rule": "some_other_dedup_rule"})
    with pytest.raises(KeyError):
        format_definition_csv_line(meta)


def test_council_summary_shares_on_time_tolerance_resolution():
    """council_summary pools the same on-time tolerance semantics as
    on_time -- both branches must resolve identically
    for identical params, so the template's footnotes can never disagree
    with what compute_council_summary actually pooled."""
    on_time_meta = resolve_definition_meta("on_time", 30, 120)
    council_meta = resolve_definition_meta("council_summary", 30, 120)
    assert council_meta.preset == on_time_meta.preset == "custom"
    assert council_meta.early_tolerance_sec == on_time_meta.early_tolerance_sec == 30
    assert council_meta.late_tolerance_sec == on_time_meta.late_tolerance_sec == 120


def test_council_summary_no_params_resolves_to_legacy_preset():
    meta = resolve_definition_meta("council_summary", None, None)
    assert meta.preset == LEGACY_PRESET_NAME
    assert meta.early_tolerance_sec is None
    assert meta.late_tolerance_sec == LEGACY_ON_TIME_LATE_TOLERANCE_SEC == 60


def test_footnotes_reflect_custom_tolerance_not_legacy_defaults():
    """A report template rendered with non-default tolerances must show
    those exact values in its footnotes, not the legacy_60s defaults --
    the same guarantee the CSV preamble already gives, split across
    separate, locale-aware lines instead of one Japanese-only cell."""
    meta = resolve_definition_meta("council_summary", 30, 120)
    lines = format_definition_footnotes(meta, "ja")
    joined = "\n".join(lines)
    assert "30秒" in joined
    assert "120秒" in joined
    assert "custom" in joined
    assert "legacy_60s" not in joined
    assert "60秒" not in joined
    assert str(MAX_PLAUSIBLE_DELAY_SEC) in joined


def test_footnotes_default_shows_legacy_preset_and_unbounded_early():
    meta = resolve_definition_meta("council_summary", None, None)
    lines = format_definition_footnotes(meta, "ja")
    joined = "\n".join(lines)
    assert "legacy_60s" in joined
    assert "無制限" in joined
    assert "60秒" in joined


def test_footnotes_are_locale_aware():
    """The same DefinitionMeta renders different text per locale -- English
    output must not leak the Japanese-only clause text (and vice versa)."""
    meta = resolve_definition_meta("council_summary", 30, 120)
    ja_lines = "\n".join(format_definition_footnotes(meta, "ja"))
    en_lines = "\n".join(format_definition_footnotes(meta, "en"))
    assert "30秒" in ja_lines and "定義" in ja_lines
    assert "30s" in en_lines and "Definition" in en_lines
    assert "定義" not in en_lines
    assert "Definition" not in ja_lines


def test_footnotes_omit_tolerance_line_for_report_types_without_one():
    """ranking/trend/etc. have no on-time/late tolerance concept at all
    (resolve_definition_meta returns preset=None, both tolerances None) --
    the footnote list must skip the tolerance/preset line entirely rather
    than render a hollow 'preset=n/a' line with nothing behind it."""
    meta = resolve_definition_meta("ranking", None, None)
    lines = format_definition_footnotes(meta, "ja")
    assert not any("プリセット" in line for line in lines)
    assert not any("定義" in line for line in lines)
    # The dedup/exclusion/measurement-point lines still apply regardless.
    assert any("集計範囲" in line for line in lines)
    assert any("重複排除" in line for line in lines)
    assert any(str(MAX_PLAUSIBLE_DELAY_SEC) in line for line in lines)


def test_footnotes_measurement_point_text_is_read_from_the_field_not_hardcoded():
    """Same guarantee as format_definition_csv_line's own test -- an
    unrecognized measurement_point must fail loudly (KeyError), not silently
    render stale text."""
    meta = resolve_definition_meta("on_time", None, None).model_copy(
        update={"measurement_point": "some_other_measurement_point"}
    )
    with pytest.raises(KeyError):
        format_definition_footnotes(meta, "ja")


def test_footnotes_dedup_rule_text_is_read_from_the_field_not_hardcoded():
    meta = resolve_definition_meta("on_time", None, None).model_copy(update={"dedup_rule": "some_other_dedup_rule"})
    with pytest.raises(KeyError):
        format_definition_footnotes(meta, "ja")
