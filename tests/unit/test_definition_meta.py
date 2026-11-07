"""Pure-logic tests for pipeline.reports.definition -- the shared "which
tolerance/dedup/exclusion rule did this report actually use" metadata block.
Mirrors compute_on_time/compute_worst_5min's own None-default resolution
(pipeline/reports/rankings.py) so the values these tests assert can never
silently drift from what a report actually computes.
"""

from datetime import date, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

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
    VALUE_BASIS,
    DefinitionMeta,
    TimetableVersion,
    format_definition_csv_line,
    format_definition_footnotes,
    format_method_stamp,
    format_stamp_lines,
    resolve_definition_meta,
)
from pipeline.reports.rankings import ON_TIME_PRESETS

# 00:30 JST on 2026-11-06, which is still 2026-11-05 in UTC.
_BUILT_AT = datetime(2026, 11, 5, 15, 30, tzinfo=timezone.utc)


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
    # is left unset: the preset names what the request asked for, and the
    # legacy default asks for no tolerance at all.
    meta = resolve_definition_meta("on_time", None, 90)
    assert meta.preset == "custom"
    assert meta.early_tolerance_sec is None
    assert meta.late_tolerance_sec == 90


def test_on_time_preset_numerically_equal_to_legacy_is_still_custom():
    # An explicit late_tolerance_sec=60 (same number as the legacy default)
    # must still resolve to "custom", not silently collapse back to
    # "legacy_60s": the preset names what the request asked for, and the
    # legacy default asks for no tolerance at all.
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


@pytest.mark.parametrize("report_type", ["on_time", "route_table", "council_summary"])
def test_the_industry_window_is_named_as_its_preset_however_it_was_asked_for(report_type):
    """The screens reach -1/+5 through the scope's early/late, never preset=,
    so the definition names the preset whose window the tolerances are."""
    assert ON_TIME_PRESETS["standard_m1_p5"] == (60, 300)
    meta = resolve_definition_meta(report_type, 60, 300)
    assert meta.preset == "standard_m1_p5"
    assert (meta.early_tolerance_sec, meta.late_tolerance_sec) == (60, 300)
    assert resolve_definition_meta(report_type, None, 300).preset == "custom"
    assert resolve_definition_meta(report_type, 60, None).preset == "custom"


def test_the_preset_is_named_on_the_csv_line():
    line = format_definition_csv_line(resolve_definition_meta("route_table", 60, 300))
    assert "プリセット=standard_m1_p5" in line
    assert "custom" not in line


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
    assert resolve_definition_meta(report_type, early, None).early_departure_sec == expected


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
    line = format_definition_csv_line(resolve_definition_meta(report_type, early, None))
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
    assert line in format_definition_footnotes(resolve_definition_meta("council_summary", early, None), locale)
    on_time = "\n".join(format_definition_footnotes(resolve_definition_meta("on_time", early, None), locale))
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
    ("locale", "line"),
    [
        ("ja", "定時の定義：定刻から1分以内の遅れ（早発も定時として数えます）"),
        ("en", "On-time definition: no more than 1 min late (early departures count as on time)"),
    ],
)
def test_footnotes_default_states_the_on_time_rule_without_the_preset_name(locale, line):
    lines = format_definition_footnotes(resolve_definition_meta("council_summary", None, None), locale)
    assert line in lines
    joined = "\n".join(lines)
    assert "legacy_60s" not in joined
    assert "プリセット" not in joined and "preset" not in joined


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


_TIMETABLES = [
    TimetableVersion(agency_id=1, name="gtfs_static_20260401", fetched_on=date(2026, 4, 1), days=28),
    TimetableVersion(agency_id=1, name=None, fetched_on=None, days=3),
]
_VALUE_BASIS = {
    "ja": "値の根拠: 事業者のフィードが停留所ごとに最後に配信した遅延の予測値から算出（通過の観測値ではない）",
    "en": "Value basis: computed from the delays the operator's feed last predicted for each stop "
    "(not observed passing times)",
}


def test_csv_line_says_what_each_value_is_before_the_method_and_ends_with_the_timetables():
    meta = _DATED.model_copy(update={"timetable_versions": _TIMETABLES})
    line = format_definition_csv_line(meta)
    assert (
        "; 値の根拠=事業者のフィードが停留所ごとに最後に配信した遅延の予測値から算出（通過の観測値ではない）; "
        f"算出方法={METHOD_VERSION}; 集計日=2026-11-06; "
        "時刻表=gtfs_static_20260401（2026-04-01取得）28日、記録なし 3日（全31日）"
    ) in line
    assert line.endswith("（全31日）")
    assert "," not in line


def test_csv_line_without_timetables_ends_with_the_build_day():
    """A definition no router stamped names no timetables, rather than none recorded."""
    assert format_definition_csv_line(_DATED).endswith("; 集計日=2026-11-06")


@pytest.mark.parametrize(
    ("locale", "timetable"),
    [
        ("ja", "時刻表: gtfs_static_20260401（2026-04-01取得）28日、記録なし 3日（全31日）"),
        ("en", "Timetable: gtfs_static_20260401 (fetched 2026-04-01) on 28 days, none recorded on 3 days (of 31 days)"),
    ],
)
def test_the_stamp_says_what_each_value_is_and_names_the_method_and_the_timetables(locale, timetable):
    meta = _DATED.model_copy(update={"timetable_versions": _TIMETABLES})
    assert format_stamp_lines(meta, locale) == [_VALUE_BASIS[locale], format_method_stamp(meta, locale), timetable]
    assert format_stamp_lines(_DATED, locale) == [_VALUE_BASIS[locale], format_method_stamp(_DATED, locale)]


@pytest.mark.parametrize(
    ("locale", "versions", "timetable"),
    [
        (
            "ja",
            [TimetableVersion(agency_id=1, name="hiroden_static", fetched_on=None, days=1)],
            "hiroden_static 1日（全1日）",
        ),
        (
            "en",
            [TimetableVersion(agency_id=1, name="hiroden_static", fetched_on=None, days=1)],
            "hiroden_static on 1 day (of 1 day)",
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
    """Each agency's versions in the order they first ran, then the days none
    was recorded on, so its entries add up to the range."""
    rows = [
        (1, "gtfs_static_20260401", 20),
        (1, "gtfs_static_20260501", 8),
        (2, "hiroden_static", 31),
        (3, None, 0),
    ]
    assert definition._timetables(rows, 31) == [
        TimetableVersion(agency_id=1, name="gtfs_static_20260401", fetched_on=date(2026, 4, 1), days=20),
        TimetableVersion(agency_id=1, name="gtfs_static_20260501", fetched_on=date(2026, 5, 1), days=8),
        TimetableVersion(agency_id=1, name=None, fetched_on=None, days=3),
        TimetableVersion(agency_id=2, name="hiroden_static", fetched_on=None, days=31),
        TimetableVersion(agency_id=3, name=None, fetched_on=None, days=31),
    ]


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
