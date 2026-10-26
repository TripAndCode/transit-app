"""The council report's prose headline, as pasted into a council document."""

import pytest

from pipeline.query.formatter import format_council_summary_footnotes, format_council_summary_text
from pipeline.reports.definition import resolve_definition_meta

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
        ("en", "On-time rate 23.6%, mean delay 2.5 min (608,834 samples), service-delivered rate 75.0%"),
        ("ja", "定時率 23.6%、平均遅延 2.5分（観測608,834件）、運行実績率 75.0%"),
    ],
)
def test_headline_groups_the_sample_count(locale, expected):
    assert _headline(_payload(), locale) == expected


@pytest.mark.parametrize(
    ("locale", "expected"),
    [
        ("en", "On-time rate 23.6%, mean delay 2.5 min (608,834 samples)"),
        ("ja", "定時率 23.6%、平均遅延 2.5分（観測608,834件）"),
    ],
)
def test_headline_leaves_out_a_rate_the_feed_cannot_measure(locale, expected):
    payload = _payload(service_delivered_pct=None)
    assert _headline(payload, locale) == expected
    text = format_council_summary_text(payload, _DEFINITION, "青森市バス", "2026-08-12", "2026-09-10", locale=locale)
    assert "—%" not in text
    unavailable = "not available for this agency's feed" if locale == "en" else "この事業者のフィードでは計測できません"
    assert unavailable in text


@pytest.mark.parametrize(
    ("locale", "expected"),
    [
        ("en", "On-time rate 23.6%, mean delay 2.5 min (608,834 samples), service-delivered rate 75.0% (whole agency)"),
        ("ja", "定時率 23.6%、平均遅延 2.5分（観測608,834件）、運行実績率 75.0%（事業者全体）"),
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
        (
            "ja",
            "計画本数・運行本数・運行実績率は、読み込み済みの時刻表がある16日分（期間30日中）だけを数えています。",
        ),
        (
            "en",
            "Planned and executed trips and the service-delivered rate count only the 16 of the period's "
            "30 days the loaded timetable covers.",
        ),
    ],
)
def test_footnotes_say_how_much_of_the_period_the_timetable_covers(locale, note):
    """A timetable loaded mid-period plans only the days it covers, so the
    planned figures say which; a fully covered period says nothing."""

    def footnotes(**coverage):
        return format_council_summary_footnotes(_DEFINITION, _payload(planned_trips=57887, **coverage), locale)

    assert note in footnotes(covered_days=16, window_days=30)
    assert note not in footnotes(covered_days=30, window_days=30)
    # No plan at all is already "unavailable"; it is not a partly covered one.
    no_plan = format_council_summary_footnotes(
        _DEFINITION, _payload(planned_trips=0, service_delivered_pct=None, covered_days=0, window_days=30), locale
    )
    assert note.replace("16", "0") not in no_plan
