"""Pure-logic tests for pipeline.reports.definition -- the shared "which
tolerance/dedup/exclusion rule did this report actually use" metadata block.
Mirrors pipeline.reports.rankings.on_time_window's default resolution, so the
values these tests assert can never silently drift from what a report
actually computes.
"""

from datetime import date, datetime, timedelta, timezone

import asyncpg
import pytest
from pydantic import ValidationError

from pipeline import static_archive
from pipeline.agg_registry import METHOD_VERSION
from pipeline.db import MAX_PLAUSIBLE_DELAY_SEC
from pipeline.histogram import (
    LEGACY_ON_TIME_LATE_TOLERANCE_SEC,
    LEGACY_PRESET_NAME,
    LEGACY_SEVERE_LATE_TOLERANCE_SEC,
)
from pipeline.reports import definition
from pipeline.reports.definition import (
    DEDUP_RULE,
    MEASUREMENT_POINT,
    METHODOLOGY_URLS,
    VALUE_BASIS,
    DefinitionMeta,
    FeedQuality,
    SourceMix,
    TimetableVersion,
    format_definition_csv_line,
    format_definition_footnotes,
    format_method_stamp,
    format_stamp_lines,
    resolve_definition_meta,
)
from pipeline.reports.rankings import DEFAULT_PRESET, ON_TIME_PRESETS, STANDARD_PRESET_NAME
from pipeline.reports.schedule_revision import ScheduleDays

# 00:30 JST on 2026-11-06, which is still 2026-11-05 in UTC.
_BUILT_AT = datetime(2026, 11, 5, 15, 30, tzinfo=timezone.utc)


def test_on_time_no_params_resolves_to_the_default_preset():
    meta = resolve_definition_meta("on_time", None, None)
    assert meta.preset == DEFAULT_PRESET == STANDARD_PRESET_NAME == "standard_m1_p5"
    assert meta.early_tolerance_sec == 60
    assert meta.late_tolerance_sec == 300


def test_the_legacy_window_keeps_its_name_and_values():
    meta = resolve_definition_meta("on_time", *ON_TIME_PRESETS[LEGACY_PRESET_NAME])
    assert meta.preset == LEGACY_PRESET_NAME == "legacy_60s"
    assert meta.early_tolerance_sec is None
    assert meta.late_tolerance_sec == LEGACY_ON_TIME_LATE_TOLERANCE_SEC == 60


def test_on_time_explicit_tolerance_resolves_to_custom_preset_with_exact_values():
    meta = resolve_definition_meta("on_time", 30, 120)
    assert meta.preset == "custom"
    assert meta.early_tolerance_sec == 30
    assert meta.late_tolerance_sec == 120


def test_on_time_explicit_late_only_resolves_to_custom_with_unbounded_early():
    # A resolved window with no early bound and a late tolerance no preset has.
    meta = resolve_definition_meta("on_time", None, 90)
    assert meta.preset == "custom"
    assert meta.early_tolerance_sec is None
    assert meta.late_tolerance_sec == 90


def test_a_window_equal_to_the_legacy_preset_is_named_legacy():
    # The scope's "count every early departure, up to 1 minute late" is the
    # legacy window, and it reads the same exact column as preset=legacy_60s,
    # so it carries the preset's name however it was asked for.
    meta = resolve_definition_meta("on_time", None, 60)
    assert meta.preset == LEGACY_PRESET_NAME
    assert meta.late_tolerance_sec == 60


def test_an_early_tolerance_without_a_late_one_is_refused():
    """Only a request's own spelling says what an unresolved half-window
    meant, so the definition takes a window already resolved."""
    with pytest.raises(ValueError):
        resolve_definition_meta("on_time", 60, None)


def test_worst_5min_no_params_names_the_default_window_whose_late_edge_it_counts_past():
    meta = resolve_definition_meta("worst_5min", None, None)
    assert meta.preset == DEFAULT_PRESET
    assert (meta.early_tolerance_sec, meta.late_tolerance_sec) == ON_TIME_PRESETS[DEFAULT_PRESET]
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


@pytest.mark.parametrize("report_type", ["on_time", "route_table", "council_summary"])
def test_the_industry_window_is_named_as_its_preset_however_it_was_asked_for(report_type):
    """The screens reach -1/+5 through the scope's early/late, never preset=,
    so the definition names the preset whose window the tolerances are."""
    assert ON_TIME_PRESETS["standard_m1_p5"] == (60, 300)
    meta = resolve_definition_meta(report_type, 60, 300)
    assert meta.preset == "standard_m1_p5"
    assert (meta.early_tolerance_sec, meta.late_tolerance_sec) == (60, 300)
    assert resolve_definition_meta(report_type, None, None).preset == "standard_m1_p5"
    assert resolve_definition_meta(report_type, None, 300).preset == "custom"


def test_the_preset_is_named_on_the_csv_line():
    line = format_definition_csv_line(resolve_definition_meta("route_table", 60, 300))
    assert "プリセット=standard_m1_p5（業界標準）; " in line
    assert "custom" not in line
    legacy = format_definition_csv_line(resolve_definition_meta("route_table", None, 60))
    assert "プリセット=legacy_60s（従来の基準）; 早着許容=無制限; 遅延許容=60秒; " in legacy
    custom = format_definition_csv_line(resolve_definition_meta("route_table", 30, 120))
    assert "プリセット=custom; " in custom


def _late(report_type: str) -> int | None:
    """A late tolerance beside an early one, for the reports with an on-time
    window, whose definition takes the window resolved."""
    return 300 if report_type in ("on_time", "route_table", "council_summary") else None


@pytest.mark.parametrize(
    ("report_type", "early", "expected"),
    [
        ("route_table", None, 60),
        ("council_summary", None, 60),
        ("route_table", 60, 60),
        ("council_summary", 30, 30),
        ("on_time", None, None),
        ("worst_5min", None, None),
        ("ranking", None, None),
    ],
)
def test_the_early_departure_threshold_is_the_early_tolerance_or_one_minute(report_type, early, expected):
    """Only the reports that show an early-departure rate name its
    threshold; with early departures counted as on time it is one minute,
    so the rate is always defined."""
    assert resolve_definition_meta(report_type, early, _late(report_type)).early_departure_sec == expected


@pytest.mark.parametrize(
    ("report_type", "early", "clause"),
    [
        ("route_table", None, "早発率=60秒を超える早発の割合"),
        ("council_summary", 30, "早発率=30秒を超える早発の割合"),
        ("on_time", None, "早発率=対象外"),
        ("ranking", None, "早発率=対象外"),
    ],
)
def test_the_csv_line_names_the_early_departure_threshold(report_type, early, clause):
    line = format_definition_csv_line(resolve_definition_meta(report_type, early, _late(report_type)))
    assert f"; {clause}; " in line


@pytest.mark.parametrize(
    ("locale", "early", "line"),
    [
        ("ja", None, "早発率：定刻より1分を超えて早く発車した割合"),
        ("en", None, "Early-departure rate: the share of departures more than 1 min early"),
        ("ja", 30, "早発率：定刻より30秒を超えて早く発車した割合"),
        ("en", 30, "Early-departure rate: the share of departures more than 30 s early"),
    ],
)
def test_footnotes_define_the_early_departure_rate(locale, early, line):
    assert line in format_definition_footnotes(resolve_definition_meta("council_summary", early, 300), locale)
    on_time = "\n".join(format_definition_footnotes(resolve_definition_meta("on_time", early, 300), locale))
    assert "早発率" not in on_time and "Early-departure" not in on_time


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


def test_csv_line_default_shows_the_industry_preset_and_both_bounds():
    meta = resolve_definition_meta("on_time", None, None)
    line = format_definition_csv_line(meta)
    assert "プリセット=standard_m1_p5（業界標準）; 早着許容=60秒; 遅延許容=300秒; " in line
    assert "legacy_60s" not in line
    assert "無制限" not in line


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


def test_council_summary_no_params_resolves_to_the_default_preset():
    meta = resolve_definition_meta("council_summary", None, None)
    assert meta.preset == DEFAULT_PRESET
    assert (meta.early_tolerance_sec, meta.late_tolerance_sec) == (60, 300)


@pytest.mark.parametrize(
    ("locale", "line"),
    [
        ("ja", "定時の定義：定刻から2分以内の遅れと、30秒以内の早発"),
        ("en", "On-time definition: no more than 2 min late and no more than 30 s early"),
    ],
)
def test_footnotes_reflect_custom_tolerance_not_legacy_defaults(locale, line):
    """A report template rendered with non-default tolerances states those
    exact values in plain words, not the defaults -- the same guarantee the
    CSV preamble gives, in a reader's terms rather than its preset name."""
    lines = format_definition_footnotes(resolve_definition_meta("council_summary", 30, 120), locale)
    assert line in lines
    joined = "\n".join(lines)
    assert "custom" not in joined
    assert "1分" not in joined and "1 min " not in joined
    assert str(MAX_PLAUSIBLE_DELAY_SEC) in joined


@pytest.mark.parametrize(
    ("locale", "default", "legacy"),
    [
        (
            "ja",
            "定時の定義（業界標準）：定刻から5分以内の遅れと、1分以内の早発",
            "定時の定義（従来の基準）：定刻から1分以内の遅れ（早発も定時として数えます）",
        ),
        (
            "en",
            "On-time definition (industry standard): no more than 5 min late and no more than 1 min early",
            "On-time definition (legacy): no more than 1 min late (early departures count as on time)",
        ),
    ],
)
def test_footnotes_name_a_preset_in_plain_words_not_its_identifier(locale, default, legacy):
    lines = format_definition_footnotes(resolve_definition_meta("council_summary", None, None), locale)
    assert default in lines
    joined = "\n".join(lines)
    assert "standard_m1_p5" not in joined
    assert "プリセット" not in joined and "preset" not in joined
    assert legacy in format_definition_footnotes(resolve_definition_meta("council_summary", None, 60), locale)


@pytest.mark.parametrize(("seconds", "ja", "en"), [(90, "1分30秒", "1 min 30 s"), (0, "0秒", "0 s")])
def test_footnotes_spell_a_tolerance_in_minutes_and_seconds(seconds, ja, en):
    meta = resolve_definition_meta("council_summary", None, seconds)
    assert f"定時の定義：定刻から{ja}以内の遅れ（早発も定時として数えます）" in format_definition_footnotes(meta, "ja")
    assert f"On-time definition: no more than {en} late (early departures count as on time)" in (
        format_definition_footnotes(meta, "en")
    )


def test_footnotes_are_locale_aware():
    """The same DefinitionMeta renders different text per locale -- English
    output must not leak the Japanese-only clause text (and vice versa)."""
    meta = resolve_definition_meta("council_summary", 30, 120)
    ja_lines = "\n".join(format_definition_footnotes(meta, "ja"))
    en_lines = "\n".join(format_definition_footnotes(meta, "en"))
    assert "30秒" in ja_lines and "定義" in ja_lines
    assert "30 s" in en_lines and "definition" in en_lines
    assert "定義" not in en_lines
    assert "definition" not in ja_lines


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


@pytest.mark.parametrize("report_type", ["on_time", "worst_5min", "ranking", "council_summary", "delay_certificate"])
def test_every_definition_names_the_method_version_and_no_build_until_told(report_type):
    """resolve_definition_meta stays pure: the build time is the caller's,
    read from agg_table_state where it holds a connection."""
    meta = resolve_definition_meta(report_type, None, None)
    assert meta.method_version == METHOD_VERSION
    assert meta.built_at is None


def test_a_build_time_without_a_zone_is_refused():
    """The stamp names the build's JST day, which a naive time cannot give."""
    with pytest.raises(ValidationError):
        DefinitionMeta(preset=None, early_tolerance_sec=None, late_tolerance_sec=None, built_at=datetime(2026, 11, 6))


def test_a_live_scan_is_marked_live():
    assert resolve_definition_meta("ranking", None, None).source == "aggregates"
    assert resolve_definition_meta("ranking", None, None, live=True).source == "live"


_DATED = resolve_definition_meta("on_time", None, None).model_copy(update={"built_at": _BUILT_AT})
_UNKNOWN = resolve_definition_meta("on_time", None, None)
_LIVE = resolve_definition_meta("on_time", None, None, live=True)


@pytest.mark.parametrize(
    ("meta", "day"),
    [(_DATED, "2026-11-06"), (_UNKNOWN, "不明"), (_LIVE, "対象外（ライブ集計）")],
    ids=["dated", "unknown", "live"],
)
def test_csv_line_names_the_method_version_and_the_jst_build_day(meta, day):
    line = format_definition_csv_line(meta)
    assert line.endswith(f"; 算出方法={METHOD_VERSION}; 集計日={day}")
    assert "," not in line


def test_a_live_definition_names_no_build_day_even_if_handed_one():
    live = _LIVE.model_copy(update={"built_at": _BUILT_AT})
    assert format_definition_csv_line(live).endswith("集計日=対象外（ライブ集計）")
    assert format_method_stamp(live, "en").endswith("not applicable (live scan)")


@pytest.mark.parametrize(
    ("locale", "dated", "unknown", "live"),
    [
        (
            "ja",
            f"算出方法: {METHOD_VERSION}、集計日: 2026-11-06",
            f"算出方法: {METHOD_VERSION}、集計日: 不明",
            f"算出方法: {METHOD_VERSION}、集計日: 対象外（ライブ集計）",
        ),
        (
            "en",
            f"Method version: {METHOD_VERSION}, aggregates built: 2026-11-06",
            f"Method version: {METHOD_VERSION}, aggregates built: unknown",
            f"Method version: {METHOD_VERSION}, aggregates built: not applicable (live scan)",
        ),
    ],
)
def test_the_method_stamp_names_the_version_and_the_jst_build_day(locale, dated, unknown, live):
    assert format_method_stamp(_DATED, locale) == dated
    assert format_method_stamp(_UNKNOWN, locale) == unknown
    assert format_method_stamp(_LIVE, locale) == live
    # A build later the same JST day is the same day.
    later = _DATED.model_copy(update={"built_at": _BUILT_AT + timedelta(hours=23)})
    assert format_method_stamp(later, locale) == dated


@pytest.mark.parametrize("meta", [_DATED, _UNKNOWN, _LIVE], ids=["dated", "unknown", "live"])
def test_the_csv_line_and_the_footnote_name_the_same_build_day(meta):
    csv_day = format_definition_csv_line(meta).rsplit("集計日=", 1)[1]
    footnote_day = format_method_stamp(meta, "ja").rsplit("集計日: ", 1)[1]
    assert csv_day == footnote_day


@pytest.mark.parametrize("locale", ["ja", "en"])
@pytest.mark.parametrize("report_type", ["council_summary", "ranking"])
def test_footnotes_end_with_the_stamp(locale, report_type):
    meta = resolve_definition_meta(report_type, None, None).model_copy(
        update={"built_at": _BUILT_AT, "timetable_versions": _TIMETABLES}
    )
    stamp = format_stamp_lines(meta, locale)
    assert format_definition_footnotes(meta, locale)[-len(stamp) :] == stamp
    assert format_method_stamp(meta, locale) in stamp


@pytest.mark.parametrize("report_type", ["on_time", "worst_5min", "ranking", "council_summary", "delay_certificate"])
@pytest.mark.parametrize("live", [False, True])
def test_every_value_is_the_feeds_prediction_and_no_timetable_is_named_until_told(report_type, live):
    """No figure is drawn from an observed passage, live or not; the timetables
    are the caller's, read where it holds a connection."""
    meta = resolve_definition_meta(report_type, None, None, live=live)
    assert meta.value_basis == VALUE_BASIS == "feed_prediction"
    assert meta.timetable_versions is None
    assert meta.source_mix is None


_TIMETABLES = [
    TimetableVersion(agency_id=1, name="gtfs_static_20260401", fetched_on=date(2026, 4, 1), days=28),
    TimetableVersion(agency_id=1, name=None, fetched_on=None, days=3),
]
_VALUE_BASIS = {
    "ja": "値の根拠: 事業者のフィードが停留所ごとに最後に配信した遅延の予測値から算出（通過の観測値ではない）",
    "en": "Value basis: computed from the delays the operator's feed last predicted for each stop "
    "(not observed passing times)",
}
_METHODOLOGY = {
    "ja": f"算出方法の説明: {METHODOLOGY_URLS['ja']}",
    "en": f"Methodology: {METHODOLOGY_URLS['en']}",
}


def test_csv_line_says_what_each_value_is_before_the_method_and_ends_with_the_timetables():
    meta = _DATED.model_copy(update={"timetable_versions": _TIMETABLES})
    line = format_definition_csv_line(meta)
    assert (
        "; 値の根拠=事業者のフィードが停留所ごとに最後に配信した遅延の予測値から算出（通過の観測値ではない）; "
        f"算出方法の説明={METHODOLOGY_URLS['ja']}; 算出方法={METHOD_VERSION}; 集計日=2026-11-06; "
        "時刻表=gtfs_static_20260401（2026-04-01取得）28日間、記録なし 3日間（全31日間）"
    ) in line
    assert line.endswith("（全31日間）")
    assert "," not in line


def test_csv_line_without_timetables_ends_with_the_build_day():
    """A definition no router stamped names no timetables, rather than none recorded."""
    assert format_definition_csv_line(_DATED).endswith("; 集計日=2026-11-06")


@pytest.mark.parametrize(
    ("locale", "timetable"),
    [
        ("ja", "時刻表: gtfs_static_20260401（2026-04-01取得）28日間、記録なし 3日間（全31日間）"),
        ("en", "Timetable: gtfs_static_20260401 (fetched 2026-04-01): 28 days; none recorded: 3 days (of 31 days)"),
    ],
)
def test_the_stamp_says_what_each_value_is_and_names_the_method_and_the_timetables(locale, timetable):
    meta = _DATED.model_copy(update={"timetable_versions": _TIMETABLES})
    assert format_stamp_lines(meta, locale) == [
        _VALUE_BASIS[locale],
        _METHODOLOGY[locale],
        format_method_stamp(meta, locale),
        timetable,
    ]
    assert format_stamp_lines(_DATED, locale) == [
        _VALUE_BASIS[locale],
        _METHODOLOGY[locale],
        format_method_stamp(_DATED, locale),
    ]


def test_every_stamp_links_the_methodology_in_the_reader_s_language():
    """The CSV preamble is Japanese, so it links the Japanese document; a
    footnote links the document in its own language."""
    assert f"; 算出方法の説明={METHODOLOGY_URLS['ja']}; " in format_definition_csv_line(_LIVE)
    for locale in ("ja", "en"):
        assert _METHODOLOGY[locale] in format_definition_footnotes(_DATED, locale)


@pytest.mark.parametrize(
    ("locale", "versions", "timetable"),
    [
        (
            "ja",
            [TimetableVersion(agency_id=1, name="hiroden_static", fetched_on=None, days=1)],
            "hiroden_static 1日間（全1日間）",
        ),
        (
            "en",
            [TimetableVersion(agency_id=1, name="hiroden_static", fetched_on=None, days=1)],
            "hiroden_static: 1 day (of 1 day)",
        ),
        ("ja", [], "記録なし"),
        ("en", [], "none recorded"),
    ],
    ids=["ja-undated", "en-undated", "ja-none", "en-none"],
)
def test_a_timetable_whose_name_carries_no_day_is_named_as_it_is(locale, versions, timetable):
    meta = _DATED.model_copy(update={"timetable_versions": versions})
    assert format_stamp_lines(meta, locale)[-1].endswith(timetable)


def test_each_listed_agency_accounts_for_every_day_of_the_range():
    """Each agency's versions in the order they first ran within the range,
    then the days none was recorded on, so its entries add up to the range.
    The day before the range, read for the trend's boundaries, is not counted."""

    def day(n):
        return date(2026, 5, 1) + timedelta(days=n)

    rows = (
        [(1, date(2026, 4, 30), "gtfs_static_20260301")]
        + [(1, day(n), "gtfs_static_20260401") for n in range(20)]
        + [(1, day(n), "gtfs_static_20260501") for n in range(20, 28)]
        + [(2, day(n), "hiroden_static") for n in range(31)]
        + [(3, None, None)]
    )
    schedule = ScheduleDays(date(2026, 5, 1), date(2026, 5, 31), rows)
    assert definition._timetables(schedule) == [
        TimetableVersion(agency_id=1, name="gtfs_static_20260401", fetched_on=date(2026, 4, 1), days=20),
        TimetableVersion(agency_id=1, name="gtfs_static_20260501", fetched_on=date(2026, 5, 1), days=8),
        TimetableVersion(agency_id=1, name=None, fetched_on=None, days=3),
        TimetableVersion(agency_id=2, name="hiroden_static", fetched_on=None, days=31),
        TimetableVersion(agency_id=3, name=None, fetched_on=None, days=31),
    ]


@pytest.mark.parametrize(
    ("archive_rows", "live_rows", "source"),
    [
        (1000, 0, "archive"),
        (0, 120, "live"),
        (900, 30, "archive"),
        (30, 900, "live"),
        (40, 40, "live"),
        (0, 0, None),
        (None, None, None),
    ],
    ids=["archive", "live", "mostly-archive", "mostly-live", "tie", "no-rows", "not-counted-yet"],
)
def test_a_day_is_filed_under_the_source_most_of_its_observations_came_from(archive_rows, live_rows, source):
    """A tie reads live, the sparser source, so a day is never called archive
    without archive rows outnumbering live ones. A day whose counts predate
    their columns has no source yet."""
    assert definition._day_source(archive_rows, live_rows) == source


def test_each_listed_agency_files_every_day_of_the_range_under_one_source():
    """Each agency's archive, live and unrecorded days add up to the range:
    days after its newest analyzed day, days analyzed before the source was
    counted, and an agency with nothing in the range are unrecorded."""

    def day(n):
        return date(2026, 5, 1) + timedelta(days=n)

    rows = (
        [(1, date(2026, 4, 30), 0, 50)]
        + [(1, day(n), 1000, 0) for n in range(20)]
        + [(1, day(n), 0, 120) for n in range(20, 23)]
        + [(1, day(23), 900, 30), (1, day(24), 40, 40), (1, day(25), None, None)]
        + [(2, None, None, None)]
    )
    assert definition._source_mix(rows, date(2026, 5, 1), date(2026, 5, 31)) == [
        SourceMix(agency_id=1, archive_days=21, live_days=4, none_days=6),
        SourceMix(agency_id=2, archive_days=0, live_days=0, none_days=31),
    ]


_SOURCE_MIX = [SourceMix(agency_id=1, archive_days=28, live_days=3, none_days=0)]
_SOURCE_NOTE = {
    "ja": "アーカイブの日は収集サーバーがその日に取得した全データ、"
    "ライブの日はアプリがリアルタイムで受け取った一部のデータのみ",
    "en": "Archive days hold every poll the collector made that day; "
    "live days hold only the part the app received in real time",
}


@pytest.mark.parametrize(
    ("locale", "observations"),
    [
        ("ja", f"観測データ: アーカイブ 28日間、ライブ 3日間（全31日間）。{_SOURCE_NOTE['ja']}"),
        ("en", f"Observations: archive 28 days, live 3 days (of 31 days). {_SOURCE_NOTE['en']}"),
    ],
)
def test_the_stamp_ends_with_the_days_each_source_supplied_and_says_what_each_means(locale, observations):
    meta = _DATED.model_copy(update={"timetable_versions": _TIMETABLES, "source_mix": _SOURCE_MIX})
    lines = format_stamp_lines(meta, locale)
    assert lines[:-1] == format_stamp_lines(_DATED.model_copy(update={"timetable_versions": _TIMETABLES}), locale)
    assert lines[-1] == observations


def test_csv_line_ends_with_the_days_each_source_supplied():
    meta = _DATED.model_copy(update={"timetable_versions": _TIMETABLES, "source_mix": _SOURCE_MIX})
    line = format_definition_csv_line(meta)
    assert line.endswith(
        "; 時刻表=gtfs_static_20260401（2026-04-01取得）28日間、記録なし 3日間（全31日間）"
        f"; 観測データ=アーカイブ 28日間、ライブ 3日間（全31日間）。{_SOURCE_NOTE['ja']}"
    )
    assert "," not in line


@pytest.mark.parametrize(
    ("locale", "mix", "observations"),
    [
        ("ja", [SourceMix(agency_id=1, archive_days=7, live_days=0, none_days=0)], "アーカイブ 7日間（全7日間）"),
        ("en", [SourceMix(agency_id=1, archive_days=7, live_days=0, none_days=0)], "archive 7 days (of 7 days)"),
        (
            "ja",
            [SourceMix(agency_id=1, archive_days=0, live_days=2, none_days=1)],
            "ライブ 2日間、記録なし 1日間（全3日間）",
        ),
        (
            "en",
            [SourceMix(agency_id=1, archive_days=0, live_days=2, none_days=1)],
            "live 2 days, none recorded 1 day (of 3 days)",
        ),
        ("ja", [SourceMix(agency_id=1, archive_days=0, live_days=0, none_days=1)], "記録なし 1日間（全1日間）"),
        ("en", [SourceMix(agency_id=1, archive_days=0, live_days=0, none_days=1)], "none recorded 1 day (of 1 day)"),
    ],
    ids=["ja-archive", "en-archive", "ja-live-unrecorded", "en-live-unrecorded", "ja-unrecorded", "en-unrecorded"],
)
def test_the_observations_line_names_only_the_sources_that_supplied_a_day(locale, mix, observations):
    """The words are explained whenever a day came from either source, and
    not under a range with none."""
    line = format_stamp_lines(_DATED.model_copy(update={"source_mix": mix}), locale)[-1]
    label = "観測データ: " if locale == "ja" else "Observations: "
    if mix[0].archive_days or mix[0].live_days:
        assert line == label + observations + ("。" if locale == "ja" else ". ") + _SOURCE_NOTE[locale]
    else:
        assert line == label + observations


@pytest.mark.parametrize(("locale", "line"), [("ja", "観測データ: 記録なし"), ("en", "Observations: none recorded")])
def test_a_scope_with_no_agency_names_no_source(locale, line):
    assert format_stamp_lines(_DATED.model_copy(update={"source_mix": []}), locale)[-1] == line


def test_a_definition_no_router_stamped_names_no_source():
    assert "観測データ" not in format_definition_csv_line(_DATED)
    assert not any(line.startswith("Observations") for line in format_stamp_lines(_DATED, "en"))


# The period's realtime completeness and clamp share, as the agencies board reads them.


def _quality(**over) -> FeedQuality:
    figures = {
        "agency_id": 1,
        "coverage_pct": 93.2,
        "observed_trips": 11500,
        "coverage_planned_trips": 12345,
        "coverage_missing": None,
        "coverage_low": False,
        "clamp_pct": 0.42,
        "clamp_missing": None,
        "clamp_high": False,
        "min_coverage_pct": 70.0,
        "max_clamp_pct": 5.0,
    }
    return FeedQuality(**{**figures, **over})


_STAMPED = _DATED.model_copy(update={"timetable_versions": _TIMETABLES, "source_mix": _SOURCE_MIX})


@pytest.mark.parametrize(
    ("locale", "line"),
    [
        ("ja", "フィード品質（事業者全体）: 観測カバー率 93.2%（計画12,345便中11,500便）、異常値 0.4%"),
        (
            "en",
            "Feed quality (whole agency): observation coverage 93.2% (11,500 of 12,345 planned trips), "
            "implausible readings 0.4%",
        ),
    ],
)
def test_the_stamp_ends_with_the_periods_completeness_and_clamp_share(locale, line):
    """Every document the stamp footnotes, the council report and the delay
    record among them, states both figures, after where the observations
    came from."""
    lines = format_stamp_lines(_STAMPED.model_copy(update={"feed_quality": [_quality()]}), locale)
    assert lines[:-1] == format_stamp_lines(_STAMPED, locale)
    assert lines[-1] == line
    footnotes = format_definition_footnotes(_STAMPED.model_copy(update={"feed_quality": [_quality()]}), locale)
    assert footnotes[-1] == line


def test_csv_line_ends_with_the_periods_completeness_and_clamp_share_in_one_cell():
    line = format_definition_csv_line(_STAMPED.model_copy(update={"feed_quality": [_quality()]}))
    assert line == format_definition_csv_line(_STAMPED) + (
        "; フィード品質（事業者全体）=観測カバー率 93.2%（計画12345便中11500便）、異常値 0.4%"
    )
    assert "," not in line


@pytest.mark.parametrize(
    ("locale", "line"),
    [
        ("ja", "フィード品質（事業者全体）: 観測カバー率 65.0%（70%未満）、異常値 6.3%（5%超）"),
        (
            "en",
            "Feed quality (whole agency): observation coverage 65.0% (below 70%), implausible readings 6.3% (above 5%)",
        ),
    ],
)
def test_a_figure_the_gate_flags_is_stated_in_the_gates_own_words(locale, line):
    """The clamp share prints to a tenth with ties away from zero, as the
    board prints it, so 6.25 reads 6.3."""
    flagged = _quality(coverage_pct=65.0, coverage_low=True, clamp_pct=6.25, clamp_high=True)
    assert format_stamp_lines(_DATED.model_copy(update={"feed_quality": [flagged]}), locale)[-1] == line


@pytest.mark.parametrize(
    ("reason", "ja", "en"),
    [
        ("no_data", "期間内の集計済みの観測なし", "no analyzed observations in the period"),
        ("unreadable", "計画を読み込めませんでした", "the plan could not be read"),
        (
            "unmatched",
            "この配信の便は時刻表の便と照合できません",
            "this feed's trips can't be matched to the timetable",
        ),
        ("no_plan", "期間に時刻表の計画なし", "no timetable plans this period"),
        ("no_plan_observed", "観測した日に時刻表の計画なし", "no timetable plans the days observed"),
    ],
)
def test_a_figure_that_cannot_be_computed_says_why_and_never_reads_0(reason, ja, en):
    missing = _quality(coverage_pct=None, observed_trips=None, coverage_planned_trips=None, coverage_missing=reason)
    meta = _DATED.model_copy(update={"feed_quality": [missing]})
    assert format_stamp_lines(meta, "ja")[-1] == f"フィード品質（事業者全体）: 観測カバー率 —（{ja}）、異常値 0.4%"
    assert format_stamp_lines(meta, "en")[-1] == (
        f"Feed quality (whole agency): observation coverage — ({en}), implausible readings 0.4%"
    )
    assert format_definition_csv_line(meta).endswith(
        f"; フィード品質（事業者全体）=観測カバー率 —（{ja}）、異常値 0.4%"
    )


def test_a_clamp_share_with_no_reading_says_so():
    meta = _DATED.model_copy(update={"feed_quality": [_quality(clamp_pct=None, clamp_missing="no_data")]})
    assert format_stamp_lines(meta, "ja")[-1].endswith("、異常値 —（期間内の集計済みの観測なし）")
    assert format_stamp_lines(meta, "en")[-1].endswith(
        ", implausible readings — (no analyzed observations in the period)"
    )


def test_several_agencies_figures_are_each_named_by_their_agency():
    meta = _DATED.model_copy(update={"feed_quality": [_quality(), _quality(agency_id=2, clamp_pct=0.0)]})
    assert format_stamp_lines(meta, "ja")[-1] == (
        "フィード品質（事業者全体）: 事業者1：観測カバー率 93.2%（計画12,345便中11,500便）、異常値 0.4%"
        "／事業者2：観測カバー率 93.2%（計画12,345便中11,500便）、異常値 0.0%"
    )
    assert format_stamp_lines(meta, "en")[-1] == (
        "Feed quality (whole agency): agency 1: observation coverage 93.2% (11,500 of 12,345 planned trips), "
        "implausible readings 0.4%; agency 2: observation coverage 93.2% (11,500 of 12,345 planned trips), "
        "implausible readings 0.0%"
    )
    assert "," not in format_definition_csv_line(meta)


def test_a_definition_without_the_figures_states_none():
    """A stamp saved before the figures were stamped, or made with no router,
    reads as it always did."""
    assert "フィード品質" not in format_definition_csv_line(_STAMPED)
    assert not any(line.startswith("Feed quality") for line in format_stamp_lines(_STAMPED, "en"))
    assert DefinitionMeta.model_validate(_STAMPED.model_dump(exclude={"feed_quality"})).feed_quality is None


def test_cached_rows_are_forgotten_whenever_the_builds_move(monkeypatch):
    """Rows cached before a build must not be served under its stamp, so the
    caches are cleared the first time a process looks and on every move after,
    including a build that lands dated before one already seen."""
    cleared: list[None] = []
    monkeypatch.setattr(definition, "clear_all", lambda: cleared.append(None))
    monkeypatch.setattr(definition, "_seen_builds", definition._UNSEEN)
    seen = [((2, 10), 1), ((2, 10), 1), ((2, 11), 2), ((3, 11), 3), ((3, 11), 3), ((0, None), 4)]
    for builds, clears in seen:
        definition._note_builds(builds)
        assert len(cleared) == clears, builds


class _BehindConn:
    """A connection to a database whose schema is behind the code: the source
    read fails with *error*, the build read answers."""

    def __init__(self, error):
        self.error = error

    def is_in_transaction(self):
        return False

    async def fetch(self, _sql, *_args):
        raise self.error("column h.archive_rows does not exist")

    async def fetchrow(self, _sql, *_args):
        return (1, 0.0, _BUILT_AT)


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [asyncpg.exceptions.UndefinedColumnError, asyncpg.exceptions.UndefinedTableError])
async def test_a_database_without_the_source_counts_stamps_everything_else(monkeypatch, error):
    monkeypatch.setattr(definition, "clear_all", lambda: None)
    monkeypatch.setattr(definition, "_seen_builds", definition._UNSEEN)
    schedule = ScheduleDays(date(2026, 5, 1), date(2026, 5, 1), [(1, date(2026, 5, 1), "gtfs_static_20260401")])
    meta = await definition.stamp_build(_BehindConn(error), resolve_definition_meta("on_time", None, None), 1, schedule)
    assert (meta.source_mix, meta.built_at) == (None, _BUILT_AT)
    assert meta.timetable_versions == [
        TimetableVersion(agency_id=1, name="gtfs_static_20260401", fetched_on=date(2026, 4, 1), days=1)
    ]


@pytest.mark.asyncio
async def test_any_other_failure_of_the_source_read_is_raised(monkeypatch):
    monkeypatch.setattr(definition, "clear_all", lambda: None)
    monkeypatch.setattr(definition, "_seen_builds", definition._UNSEEN)
    schedule = ScheduleDays(date(2026, 5, 1), date(2026, 5, 1), [])
    with pytest.raises(asyncpg.exceptions.QueryCanceledError):
        await definition.stamp_build(
            _BehindConn(asyncpg.exceptions.QueryCanceledError),
            resolve_definition_meta("on_time", None, None),
            1,
            schedule,
        )


# Each named timetable's short content hash, from the Postgres mirror of the version map.

_HASHED = [
    TimetableVersion(
        agency_id=1, name="gtfs_static_20260401", fetched_on=date(2026, 4, 1), days=20, content_hash="a1b2c3d4"
    ),
    TimetableVersion(agency_id=1, name="hiroden_static", fetched_on=None, days=5, content_hash="e5f60718"),
    TimetableVersion(agency_id=1, name="gtfs_static_20260501", fetched_on=date(2026, 5, 1), days=3),
    TimetableVersion(agency_id=1, name=None, fetched_on=None, days=3),
]


def test_the_csv_line_names_each_mapped_timetable_by_its_content_too():
    line = format_definition_csv_line(_DATED.model_copy(update={"timetable_versions": _HASHED}))
    assert line.endswith(
        "; 時刻表=gtfs_static_20260401（2026-04-01取得、内容ハッシュ a1b2c3d4）20日間、"
        "hiroden_static（内容ハッシュ e5f60718）5日間、gtfs_static_20260501（2026-05-01取得）3日間、"
        "記録なし 3日間（全31日間）"
    )
    assert "," not in line


@pytest.mark.parametrize(
    ("locale", "timetable"),
    [
        (
            "ja",
            "時刻表: gtfs_static_20260401（2026-04-01取得、内容ハッシュ a1b2c3d4）20日間、"
            "hiroden_static（内容ハッシュ e5f60718）5日間、gtfs_static_20260501（2026-05-01取得）3日間、"
            "記録なし 3日間（全31日間）",
        ),
        (
            "en",
            "Timetable: gtfs_static_20260401 (fetched 2026-04-01, content hash a1b2c3d4): 20 days; "
            "hiroden_static (content hash e5f60718): 5 days; gtfs_static_20260501 (fetched 2026-05-01): 3 days; "
            "none recorded: 3 days (of 31 days)",
        ),
    ],
)
def test_the_footnotes_name_each_mapped_timetable_by_its_content_too(locale, timetable):
    meta = _DATED.model_copy(update={"timetable_versions": _HASHED})
    assert format_stamp_lines(meta, locale)[-1] == timetable
    assert format_definition_footnotes(meta, locale)[-1] == timetable


def test_each_named_timetable_takes_the_short_hash_of_its_own_agencys_content_and_no_other():
    versions = [
        TimetableVersion(agency_id=1, name="v1", fetched_on=None, days=2),
        TimetableVersion(agency_id=1, name="v2", fetched_on=None, days=1),
        TimetableVersion(agency_id=2, name="v1", fetched_on=None, days=3),
        TimetableVersion(agency_id=2, name=None, fetched_on=None, days=1),
    ]
    contents = {(1, "v1"): "0123456789abcdef" * 2, (2, "v2"): "f" * 32}
    assert definition._with_contents(versions, contents) == [
        versions[0].model_copy(update={"content_hash": "01234567"}),
        *versions[1:],
    ]


class _MirrorConn:
    """A connection whose mirror holds *contents* ({(agency, name): content}),
    each mapped before the agency's aggregates were built but those named in
    *unbuilt*, or that lacks the mirror when *error* is given; it records each
    mirror read."""

    def __init__(self, contents=None, error=None, unbuilt=()):
        self.contents = contents or {}
        self.error = error
        self.unbuilt = set(unbuilt)
        self.reads: list[tuple] = []

    def is_in_transaction(self):
        return False

    async def fetch(self, sql, *args):
        if "static_version_contents" not in sql:
            return []
        self.reads.append(args)
        if self.error is not None:
            raise self.error('relation "static_version_contents" does not exist')
        agencies, names, tables = args
        assert set(tables) == set(static_archive.archive_joined_tables())
        return [
            (agency, name, self.contents[(agency, name)], (agency, name) not in self.unbuilt)
            for agency, name in zip(agencies, names, strict=True)
            if (agency, name) in self.contents
        ]

    async def fetchrow(self, _sql, *_args):
        return (1, 0.0, _BUILT_AT)


def _schedule(rows):
    return ScheduleDays(date(2026, 5, 1), date(2026, 5, 2), rows)


@pytest.mark.asyncio
async def test_the_stamp_reads_the_content_of_each_name_in_force_from_postgres(monkeypatch):
    monkeypatch.setattr(definition, "clear_all", lambda: None)
    conn = _MirrorConn({(1, "gtfs_static_20260401"): "a1b2c3d4" + "0" * 24, (1, "unused"): "f" * 32})
    schedule = _schedule([(1, date(2026, 5, 1), "gtfs_static_20260401"), (1, date(2026, 5, 2), "hiroden_static")])
    meta = await definition.stamp_build(conn, resolve_definition_meta("on_time", None, None), 1, schedule)
    assert meta.timetable_versions == [
        TimetableVersion(
            agency_id=1, name="gtfs_static_20260401", fetched_on=date(2026, 4, 1), days=1, content_hash="a1b2c3d4"
        ),
        TimetableVersion(agency_id=1, name="hiroden_static", fetched_on=None, days=1),
    ]
    assert [read[:2] for read in conn.reads] == [([1, 1], ["gtfs_static_20260401", "hiroden_static"])]


@pytest.mark.asyncio
@pytest.mark.parametrize(("live", "content"), [(False, None), (True, "a1b2c3d4")])
async def test_a_content_mapped_since_the_build_is_named_beside_a_live_scan_alone(monkeypatch, live, content):
    """Aggregates built before a name's content was mapped hold figures of
    another content, so the stamp beside them names none; a live scan names
    the content its own join filed the rows under."""
    monkeypatch.setattr(definition, "clear_all", lambda: None)
    conn = _MirrorConn({(1, "gtfs_static_20260401"): "a1b2c3d4" + "0" * 24}, unbuilt={(1, "gtfs_static_20260401")})
    schedule = _schedule([(1, date(2026, 5, 1), "gtfs_static_20260401")])
    joined = {(1, "gtfs_static_20260401"): "a1b2c3d4" + "0" * 24} if live else None
    meta = await definition.stamp_build(
        conn, resolve_definition_meta("on_time", None, None, live=live), 1, schedule, joined=joined
    )
    assert meta.timetable_versions[0].content_hash == content


@pytest.mark.asyncio
async def test_a_live_stamp_names_the_content_of_the_join_that_filed_its_rows_and_never_reads_the_mirror(
    monkeypatch,
):
    """The mirror may already hold a remap the scan's cached join has not
    seen, or still lag one it has: the stamp names what the join filed."""
    monkeypatch.setattr(definition, "clear_all", lambda: None)
    conn = _MirrorConn({(1, "gtfs_static_20260401"): "f" * 32})
    schedule = _schedule([(1, date(2026, 5, 1), "gtfs_static_20260401"), (1, date(2026, 5, 2), "hiroden_static")])
    live = resolve_definition_meta("on_time", None, None, live=True)
    meta = await definition.stamp_build(
        conn, live, 1, schedule, joined={(1, "gtfs_static_20260401"): "a1b2c3d4" + "0" * 24}
    )
    assert [(v.name, v.content_hash) for v in meta.timetable_versions] == [
        ("gtfs_static_20260401", "a1b2c3d4"),
        ("hiroden_static", None),
    ]
    assert conn.reads == []


@pytest.mark.asyncio
async def test_a_live_stamp_with_no_join_names_no_content(monkeypatch):
    """An agency whose ingest stamps no version has no join: its rows keep the
    timetable they were ingested against, which no content names."""
    monkeypatch.setattr(definition, "clear_all", lambda: None)
    conn = _MirrorConn({(1, "gtfs_static_20260401"): "a1b2c3d4" + "0" * 24})
    schedule = _schedule([(1, date(2026, 5, 1), "gtfs_static_20260401")])
    meta = await definition.stamp_build(conn, resolve_definition_meta("on_time", None, None, live=True), 1, schedule)
    assert meta.timetable_versions[0].content_hash is None
    assert conn.reads == []


@pytest.mark.asyncio
async def test_a_stamp_naming_no_timetable_reads_no_content(monkeypatch):
    monkeypatch.setattr(definition, "clear_all", lambda: None)
    conn = _MirrorConn()
    meta = await definition.stamp_build(
        conn, resolve_definition_meta("on_time", None, None), 1, _schedule([(1, None, None)])
    )
    assert meta.timetable_versions == [TimetableVersion(agency_id=1, name=None, fetched_on=None, days=2)]
    assert conn.reads == []


@pytest.mark.asyncio
async def test_a_database_without_the_mirror_names_its_timetables_with_no_hash(monkeypatch):
    monkeypatch.setattr(definition, "clear_all", lambda: None)
    conn = _MirrorConn(error=asyncpg.exceptions.UndefinedTableError)
    schedule = _schedule([(1, date(2026, 5, 1), "gtfs_static_20260401")])
    meta = await definition.stamp_build(conn, resolve_definition_meta("on_time", None, None), 1, schedule)
    assert meta.timetable_versions == [
        TimetableVersion(agency_id=1, name="gtfs_static_20260401", fetched_on=date(2026, 4, 1), days=1),
        TimetableVersion(agency_id=1, name=None, fetched_on=None, days=1),
    ]
    assert meta.source_mix is not None
