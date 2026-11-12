"""The council report's prose headline, as pasted into a council document."""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import get_args

import pytest

from pipeline.agg_registry import METHOD_VERSION
from pipeline.query.formatter import format_council_summary_footnotes, format_council_summary_text
from pipeline.reports.definition import FeedQuality, resolve_definition_meta
from pipeline.reports.service_delivered import DeliveredMissing

_DEFINITION = resolve_definition_meta("council_summary", None, None)


def _payload(**overrides):
    payload = {
        "on_time_pct": 23.6,
        "avg_delay_min": 2.5,
        "samples": 608834,
        "service_delivered_pct": 75.0,
        "is_stale": False,
        "clamp_pct": 0,
    }
    payload.update(overrides)
    return payload


def _headline(payload, locale):
    text = format_council_summary_text(payload, _DEFINITION, "青森市バス", "2026-08-12", "2026-09-10", locale=locale)
    return text.split("\n")[1]


@pytest.mark.parametrize(
    ("locale", "expected"),
    [
        ("en", "On-time rate 23.6%, mean delay 2.5 min (608,834 stop-level observations), service delivered 75.0%"),
        ("ja", "定時率 23.6%、平均遅延 2.5分（観測データ608,834件・停留所単位）、運行率 75.0%"),
    ],
)
def test_headline_groups_the_sample_count(locale, expected):
    assert _headline(_payload(), locale) == expected


@pytest.mark.parametrize(
    ("locale", "expected"),
    [
        (
            "en",
            "On-time rate 23.6%, early departures 4.2% (more than 1 min early), mean delay 2.5 min "
            "(608,834 stop-level observations), service delivered 75.0%",
        ),
        (
            "ja",
            "定時率 23.6%、早発率 4.2%（1分を超える早発）、"
            "平均遅延 2.5分（観測データ608,834件・停留所単位）、運行率 75.0%",
        ),
    ],
)
def test_headline_states_the_early_departure_rate_with_its_threshold(locale, expected):
    assert _headline(_payload(early_departure_pct=4.2), locale) == expected


@pytest.mark.parametrize(
    ("locale", "words", "footnote"),
    [
        (
            "en",
            "early departures 4.2% (more than 30 s early)",
            "* Early-departure rate: the share of departures more than 30 s early",
        ),
        ("ja", "早発率 4.2%（30秒を超える早発）", "※早発率：定刻より30秒を超えて早く発車した割合"),
    ],
)
def test_a_limited_early_tolerance_is_the_early_departure_threshold(locale, words, footnote):
    definition = resolve_definition_meta("council_summary", 30, 300)
    text = format_council_summary_text(
        _payload(early_departure_pct=4.2), definition, "青森市バス", "2026-08-12", "2026-09-10", locale=locale
    )
    assert words in text.split("\n")[1]
    assert footnote in text.split("\n")


@pytest.mark.parametrize(
    ("locale", "expected"),
    [
        ("en", "On-time rate 23.6%, mean delay 2.5 min (608,834 stop-level observations)"),
        ("ja", "定時率 23.6%、平均遅延 2.5分（観測データ608,834件・停留所単位）"),
    ],
)
def test_headline_leaves_out_a_rate_the_feed_cannot_measure(locale, expected):
    payload = _payload(service_delivered_pct=None, delivered_missing="feed_unverified")
    assert _headline(payload, locale) == expected
    text = format_council_summary_text(payload, _DEFINITION, "青森市バス", "2026-08-12", "2026-09-10", locale=locale)
    assert "—%" not in text


_BOARD_REASONS = {
    locale: json.loads(
        (Path(__file__).resolve().parents[2] / f"frontend/src/i18n/locales/{locale}.json").read_text("utf-8")
    )["network"]["delivered_missing"]
    for locale in ("ja", "en")
}


@pytest.mark.parametrize("reason", get_args(DeliveredMissing))
@pytest.mark.parametrize(
    ("locale", "template"),
    [("ja", "運行率：—（{}）"), ("en", "Service delivered %: — ({})")],
)
def test_footnotes_give_the_missing_rate_reason_in_the_agencies_board_words(reason, locale, template):
    """A missing rate says why, in the same words the agencies board shows
    beside its own missing rate, never a blanket "cannot be measured"."""
    payload = _payload(service_delivered_pct=None, delivered_missing=reason)
    lines = format_council_summary_footnotes(_DEFINITION, payload, locale)
    assert template.format(_BOARD_REASONS[locale][reason]) in lines


@pytest.mark.parametrize(
    ("locale", "line"),
    [
        ("ja", "運行率：—（この配信は運休を伝えていません）"),
        ("en", "Service delivered %: — (this feed doesn't report cancellations)"),
    ],
)
def test_footnotes_say_a_feed_that_sends_no_cancellations_has_no_rate(locale, line):
    payload = _payload(service_delivered_pct=None, delivered_missing="feed_reports_no_cancellations")
    assert line in format_council_summary_footnotes(_DEFINITION, payload, locale)


@pytest.mark.parametrize("locale", ["ja", "en"])
def test_footnotes_leave_a_measured_rate_without_a_reason(locale):
    lines = format_council_summary_footnotes(_DEFINITION, _payload(), locale)
    assert not any(line.startswith(("運行率：", "Service delivered %:")) for line in lines)


@pytest.mark.parametrize(
    ("locale", "header"),
    [
        ("ja", "【協議会向け報告（青森市バス、2026-08-12 〜 2026-09-10）】"),
        ("en", "[Council report (青森市バス, 2026-08-12 to 2026-09-10)]"),
    ],
)
def test_header_names_the_document_as_the_page_does(locale, header):
    text = format_council_summary_text(_payload(), _DEFINITION, "青森市バス", "2026-08-12", "2026-09-10", locale=locale)
    assert text.split("\n")[0] == header


@pytest.mark.parametrize(
    ("locale", "expected"),
    [
        (
            "en",
            "On-time rate 23.6%, mean delay 2.5 min (608,834 stop-level observations), "
            "service delivered 75.0% (whole agency)",
        ),
        ("ja", "定時率 23.6%、平均遅延 2.5分（観測データ608,834件・停留所単位）、運行率 75.0%（事業者全体）"),
    ],
)
def test_headline_labels_the_delivered_rate_agency_wide_under_a_narrower_filter(locale, expected):
    assert _headline(_payload(filtered=True), locale) == expected


@pytest.mark.parametrize(
    ("locale", "note"),
    [
        ("en", "cover the whole agency for the period"),
        ("ja", "事業者全体の値です"),
    ],
)
@pytest.mark.parametrize("delivered", [75.0, None])
def test_footnotes_say_the_delivered_figures_are_agency_wide_under_a_narrower_filter(locale, note, delivered):
    payload = _payload(filtered=True, service_delivered_pct=delivered)
    assert any(note in line for line in format_council_summary_footnotes(_DEFINITION, payload, locale))
    unfiltered = _payload(service_delivered_pct=delivered)
    assert not any(note in line for line in format_council_summary_footnotes(_DEFINITION, unfiltered, locale))


@pytest.mark.parametrize(
    ("locale", "note"),
    [
        ("ja", "計画便数・運行便数・運行率は、時刻表に計画がある16/30日分だけを数えています。"),
        (
            "en",
            "Planned and executed trips and Service delivered % count only the 16/30 days a timetable plans.",
        ),
    ],
)
def test_footnotes_say_how_much_of_the_period_the_timetable_covers(locale, note):
    """Only the days a timetable plans are counted: the days the loaded one
    covers and the days saved while theirs was loaded. The footnote says how
    many; a fully covered period says nothing."""

    def footnotes(**coverage):
        return format_council_summary_footnotes(_DEFINITION, _payload(planned_trips=57887, **coverage), locale)

    assert note in footnotes(covered_days=16, window_days=30)
    assert note not in footnotes(covered_days=30, window_days=30)
    # No plan at all is already "unavailable"; it is not a partly covered one.
    no_plan = format_council_summary_footnotes(
        _DEFINITION, _payload(planned_trips=0, service_delivered_pct=None, covered_days=0, window_days=30), locale
    )
    assert note.replace("16", "0") not in no_plan


_BOARD = {
    locale: json.loads(
        (Path(__file__).resolve().parents[2] / f"frontend/src/i18n/locales/{locale}.json").read_text("utf-8")
    )["network"]
    for locale in ("ja", "en")
}

_NOT_COMPARABLE_LEAD = {
    "ja": "他事業者との比較: 条件を満たしません。",
    "en": "Comparison with other operators: does not meet the conditions. ",
}
_NOT_COMPARABLE_TAIL = {
    "ja": "。この事業者の数字を他事業者と順位付けて比べないでください。",
    "en": ". Do not rank this operator's figures against other operators'.",
}


def _comparability(**overrides):
    comparability = {
        "comparable": True,
        "reason": None,
        "coverage_pct": 86.3,
        "observed_trips": 863,
        "coverage_planned_trips": 1000,
        "ci_low": 83.9,
        "ci_high": 86.4,
        "clamp_pct": 0.2,
        "planned_trips": 1000,
        "delivered_missing": None,
    }
    comparability.update(overrides)
    return comparability


def _board_words(locale, template, **values):
    """The agencies board's own wording for a reason, filled in as the board
    fills it."""
    for name, value in values.items():
        template = template.replace("{{" + name + "}}", value)
    return template


@pytest.mark.parametrize(
    ("locale", "line"),
    [
        ("ja", "※他事業者との比較: 比較の条件を満たします（観測カバー率 86.3%、定時率の95%信頼区間 83.9–86.4%）"),
        (
            "en",
            "* Comparison with other operators: meets the conditions for a comparison "
            "(observation coverage 86.3%, on-time % 95% interval 83.9–86.4%)",
        ),
    ],
)
def test_a_comparable_agency_is_footnoted_with_the_figures_the_gate_judged(locale, line):
    payload = _payload(comparability=_comparability())
    text = format_council_summary_text(payload, _DEFINITION, "青森市バス", "2026-08-12", "2026-09-10", locale=locale)
    assert line in text.split("\n")


@pytest.mark.parametrize(
    ("locale", "line"),
    [
        (
            "ja",
            "他事業者との比較: 条件を満たしません。観測カバー率 41.0%（70%未満）。"
            "この事業者の数字を他事業者と順位付けて比べないでください。",
        ),
        (
            "en",
            "Comparison with other operators: does not meet the conditions. Observation coverage 41.0% "
            "(below 70%). Do not rank this operator's figures against other operators'.",
        ),
    ],
)
def test_an_agency_the_board_would_not_rank_is_footnoted_not_to_be_ranked(locale, line):
    payload = _payload(comparability=_comparability(comparable=False, reason="coverage_low", coverage_pct=41.0))
    assert line in format_council_summary_footnotes(_DEFINITION, payload, locale)


@pytest.mark.parametrize("locale", ["ja", "en"])
@pytest.mark.parametrize(
    ("reason", "figures", "values"),
    [
        ("no_data", {"coverage_pct": None, "ci_low": None, "ci_high": None, "clamp_pct": None}, {}),
        ("coverage_low", {"coverage_pct": 41.0}, {"pct": "41.0", "min": "70"}),
        # Half of 6.1 is 3.05, which prints rounded up, never at the limit.
        ("ci_wide", {"ci_low": 80.0, "ci_high": 86.1}, {"half": "3.1", "max": "3"}),
        ("ci_wide", {"ci_low": None, "ci_high": None}, {"half": "—", "max": "3"}),
        # 5.05 prints 5.1, as the board prints it.
        ("clamp_high", {"clamp_pct": 5.05}, {"pct": "5.1", "max": "5"}),
    ],
)
def test_a_not_comparable_footnote_gives_the_reason_in_the_agencies_board_words(locale, reason, figures, values):
    payload = _payload(comparability=_comparability(comparable=False, reason=reason, **figures))
    words = _board_words(locale, _BOARD[locale]["unranked_reason"][reason], **values)
    expected = _NOT_COMPARABLE_LEAD[locale] + words + _NOT_COMPARABLE_TAIL[locale]
    assert expected in format_council_summary_footnotes(_DEFINITION, payload, locale)


@pytest.mark.parametrize("locale", ["ja", "en"])
@pytest.mark.parametrize(
    ("why", "state", "coverage_planned_trips"),
    [
        # A feed whose trip_ids are not the timetable's: the gate drops the planned count.
        ("unmatched", {"planned_trips": 1000, "delivered_missing": "feed_cannot_report"}, None),
        ("no_plan", {"planned_trips": 0, "delivered_missing": "no_plan"}, 0),
        # A plan only for days after any agency's observations reach.
        ("no_plan_observed", {"planned_trips": 1000, "delivered_missing": None}, 0),
        # A failed read nulls the planned count too, so it is told apart first.
        ("unreadable", {"planned_trips": 0, "delivered_missing": "unreadable"}, None),
    ],
)
def test_a_coverage_unknown_footnote_says_why_coverage_cannot_be_measured(locale, why, state, coverage_planned_trips):
    """The reason reads only the verdict's own plan figures, the board's, so
    one verdict's "why" comes from one read: the report's own planned figures
    here say otherwise and are not consulted."""
    comparability = _comparability(
        comparable=False,
        reason="coverage_unknown",
        coverage_pct=None,
        observed_trips=None,
        coverage_planned_trips=coverage_planned_trips,
        ci_low=None,
        ci_high=None,
        **state,
    )
    elsewhere = (
        {"planned_trips": 1000, "delivered_missing": None}
        if why == "unreadable"
        else {"planned_trips": 0, "delivered_missing": "unreadable"}
    )
    payload = _payload(comparability=comparability, **elsewhere)
    words = _board_words(
        locale,
        _BOARD[locale]["unranked_reason"]["coverage_unknown"],
        why=_BOARD[locale]["coverage_unknown_why"][why],
    )
    expected = _NOT_COMPARABLE_LEAD[locale] + words + _NOT_COMPARABLE_TAIL[locale]
    assert expected in format_council_summary_footnotes(_DEFINITION, payload, locale)


@pytest.mark.parametrize("locale", ["ja", "en"])
def test_a_payload_without_the_gate_says_nothing_about_a_comparison(locale):
    lines = format_council_summary_footnotes(_DEFINITION, _payload(), locale)
    assert not any(line.startswith(("他事業者との比較", "Comparison with other operators")) for line in lines)


@pytest.mark.parametrize(
    ("locale", "note"),
    [
        (
            "ja",
            "計画便数・運行便数・運行率・データ品質と他事業者との比較は、系統・曜日・ダイヤ・時間帯の絞り込みによらず、"
            "この期間の事業者全体の値です。",
        ),
        (
            "en",
            "Planned and executed trips, Service delivered %, data quality and the comparison with other operators "
            "cover the whole agency for the period; the route, day, service and time-band filters apply to the "
            "on-time figures only.",
        ),
    ],
)
def test_the_agency_wide_note_counts_the_comparison_among_the_whole_agency_figures(locale, note):
    payload = _payload(filtered=True, comparability=_comparability())
    assert note in format_council_summary_footnotes(_DEFINITION, payload, locale)


_SAME_DEFINITION_NOTE = {
    "ja": "他事業者との比較は、ここで指定した定時の幅ではなく、どの事業者にも共通の定時の定義で判定しています。",
    "en": "The comparison with other operators judges every operator by one shared on-time definition, "
    "not the tolerance chosen here.",
}


@pytest.mark.parametrize("locale", ["ja", "en"])
def test_a_custom_tolerance_says_the_comparison_keeps_the_shared_definition(locale):
    """The gate reads the board's figures, which count on time by one
    definition for every agency, so a report counted with another tolerance
    says its interval is not the headline's."""
    payload = _payload(comparability=_comparability())
    custom = resolve_definition_meta("council_summary", 45, 90)
    assert _SAME_DEFINITION_NOTE[locale] in format_council_summary_footnotes(custom, payload, locale)
    assert _SAME_DEFINITION_NOTE[locale] not in format_council_summary_footnotes(_DEFINITION, payload, locale)
    # A tolerance spelled out as the default one is still the shared definition.
    spelled_out = resolve_definition_meta("council_summary", 60, 300)
    assert _SAME_DEFINITION_NOTE[locale] not in format_council_summary_footnotes(spelled_out, payload, locale)
    # The legacy preset is another definition than the board's.
    legacy = resolve_definition_meta("council_summary", None, 60)
    assert _SAME_DEFINITION_NOTE[locale] in format_council_summary_footnotes(legacy, payload, locale)
    without_gate = format_council_summary_footnotes(custom, _payload(), locale)
    assert _SAME_DEFINITION_NOTE[locale] not in without_gate


@pytest.mark.parametrize(
    ("locale", "dated", "live"),
    [
        (
            "ja",
            f"※算出方法: {METHOD_VERSION}、集計日: 2026-11-06",
            f"※算出方法: {METHOD_VERSION}、集計日: 対象外（ライブ集計）",
        ),
        (
            "en",
            f"* Method version: {METHOD_VERSION}, aggregates built: 2026-11-06",
            f"* Method version: {METHOD_VERSION}, aggregates built: not applicable (live scan)",
        ),
    ],
)
def test_the_report_is_footnoted_with_the_method_version_and_the_build_day(locale, dated, live):
    """A council document names the method its figures were made with and the
    JST day their aggregates were built, so a later rebuild can be told apart,
    or that its figures came from a live scan with no build behind them."""
    built = _DEFINITION.model_copy(update={"built_at": datetime(2026, 11, 5, 15, 30, tzinfo=timezone.utc)})
    scanned = resolve_definition_meta("council_summary", None, None, live=True)

    def lines(definition):
        text = format_council_summary_text(_payload(), definition, "青森市バス", "2026-08-12", "2026-09-10", locale)
        return text.split("\n")

    assert dated in lines(built)
    assert live in lines(scanned)


_STAMPED_QUALITY = FeedQuality(
    agency_id=1,
    coverage_pct=90.0,
    observed_trips=900,
    coverage_planned_trips=1000,
    coverage_missing=None,
    coverage_low=False,
    clamp_pct=1.5,
    clamp_missing=None,
    clamp_high=False,
    min_coverage_pct=70.0,
    max_clamp_pct=5.0,
)


@pytest.mark.parametrize(
    ("locale", "stamped", "caveat"),
    [
        (
            "ja",
            "※フィード品質（事業者全体）: 観測カバー率 90.0%（計画1,000便中900便）、異常値 1.5%",
            "※データ品質: 観測値の1.5%が異常値として除外されています。",
        ),
        (
            "en",
            "* Feed quality (whole agency): observation coverage 90.0% (900 of 1,000 planned trips), "
            "implausible readings 1.5%",
            "* Data quality: 1.5% of observations were excluded as implausible.",
        ),
    ],
)
def test_a_stamped_report_states_its_clamp_share_once(locale, stamped, caveat):
    """The stamp's line states the period's clamp share beside its coverage, so
    the report's own quality note would print the same share a second time; a
    definition stamped without the figures keeps the note."""
    definition = _DEFINITION.model_copy(update={"feed_quality": [_STAMPED_QUALITY]})

    def lines(definition):
        payload = _payload(clamp_pct=1.5)
        return format_council_summary_text(payload, definition, "青森市バス", "2026-08-12", "2026-09-10", locale).split(
            "\n"
        )

    assert stamped in lines(definition)
    assert caveat not in lines(definition)
    assert caveat in lines(_DEFINITION)
    unavailable = format_council_summary_footnotes(definition, _payload(clamp_pct=None), locale)
    assert not any(line.startswith(("データ品質", "Data quality")) for line in unavailable)
