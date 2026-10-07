"""The council report's prose headline, as pasted into a council document."""

import pytest

from pipeline.query.formatter import format_council_summary_text
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
