"""The council report's prose headline, as pasted into a council document."""

import json
from pathlib import Path
from typing import get_args

import pytest

from pipeline.query.formatter import format_council_summary_footnotes, format_council_summary_text
from pipeline.reports.definition import resolve_definition_meta
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
