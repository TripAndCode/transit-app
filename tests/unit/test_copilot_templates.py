import re

import pytest

from pipeline.query.copilot_templates import NO_SIGNAL_TEMPLATE_ID, render_template

OVERVIEW_PAYLOAD = {
    "headline": {"avg_min": 6.4, "baseline_avg_min": 4.1, "delta_min": 2.3, "delta_pct": 56.1, "samples": 812},
    "top_delayed": {
        "routes": [
            {"route_code": "R12", "route_short_name": "12", "avg_min": 14.2},
            {"route_code": "R7", "route_short_name": "7", "avg_min": 9.8},
        ],
        "delayed_count": 6,
    },
}

PAYLOAD_NUMBERS = {"14.2", "12", "9.8", "7", "6.4", "4.1", "2.3", "56.1", "812", "6"}


@pytest.mark.parametrize("locale", ["ja", "en"])
def test_overview_top_delay_route_interpolates_only_payload_numbers(locale):
    """The zero-numeric-hallucination guarantee must hold in every locale."""
    rendered = render_template("overview_top_delay_route", {}, OVERVIEW_PAYLOAD, locale)
    assert "14.2" in rendered["text"]
    for match in re.findall(r"\d+\.?\d*", rendered["text"]):
        assert match in PAYLOAD_NUMBERS, f"unexplained number {match!r} in {rendered['text']!r}"


def test_overview_top_delay_route_defaults_to_japanese():
    """Default locale is ja, matching pipeline.query.tools._summary."""
    explicit = render_template("overview_top_delay_route", {}, OVERVIEW_PAYLOAD, "ja")
    assert render_template("overview_top_delay_route", {}, OVERVIEW_PAYLOAD) == explicit


def test_overview_top_delay_route_ja_renders_japanese():
    rendered = render_template("overview_top_delay_route", {}, OVERVIEW_PAYLOAD, "ja")
    assert "路線" in rendered["text"]
    assert "増加" in rendered["text"]
    assert "Route" not in rendered["text"]
    assert "概況" in rendered["cite"]


def test_overview_top_delay_route_ja_shows_decrease_when_delay_improved():
    payload = {**OVERVIEW_PAYLOAD, "headline": {**OVERVIEW_PAYLOAD["headline"], "delta_pct": -12.3}}
    rendered = render_template("overview_top_delay_route", {}, payload, "ja")
    assert "減少" in rendered["text"]
    assert "増加" not in rendered["text"]
    assert "-12.3" not in rendered["text"]


def test_overview_top_delay_route_en_renders_english():
    rendered = render_template("overview_top_delay_route", {}, OVERVIEW_PAYLOAD, "en")
    assert "Route 12" in rendered["text"]
    assert "up 56.1%" in rendered["text"]
    assert rendered["cite"] == "Overview · 812 samples · top_delayed[0]"


def test_overview_top_delay_route_en_shows_down_when_delay_improved():
    payload = {**OVERVIEW_PAYLOAD, "headline": {**OVERVIEW_PAYLOAD["headline"], "delta_pct": -12.3}}
    rendered = render_template("overview_top_delay_route", {}, payload, "en")
    assert "down 12.3%" in rendered["text"]
    assert "up" not in rendered["text"]
    assert "-12.3" not in rendered["text"]


def test_unsupported_locale_falls_back_to_japanese():
    """Mirrors _summary: an unknown locale renders ja rather than raising."""
    fallback = render_template("overview_top_delay_route", {}, OVERVIEW_PAYLOAD, "fr")
    assert fallback == render_template("overview_top_delay_route", {}, OVERVIEW_PAYLOAD, "ja")


@pytest.mark.parametrize("locale", ["ja", "en"])
def test_no_signal_template_has_no_numbers(locale):
    rendered = render_template(NO_SIGNAL_TEMPLATE_ID, {}, OVERVIEW_PAYLOAD, locale)
    assert re.findall(r"\d", rendered["text"]) == []


def test_no_signal_template_is_localized():
    ja = render_template(NO_SIGNAL_TEMPLATE_ID, {}, OVERVIEW_PAYLOAD, "ja")
    en = render_template(NO_SIGNAL_TEMPLATE_ID, {}, OVERVIEW_PAYLOAD, "en")
    assert ja["text"] != en["text"]
    assert "目立った" in ja["text"]
    assert en["text"].startswith("Nothing stands out")


def test_unknown_template_id_raises():
    with pytest.raises(KeyError):
        render_template("not_a_real_template", {}, OVERVIEW_PAYLOAD, "ja")


def test_overview_top_delay_route_requires_top_delayed_routes():
    with pytest.raises(KeyError):
        render_template("overview_top_delay_route", {}, {"headline": OVERVIEW_PAYLOAD["headline"]}, "ja")


def test_overview_top_delay_route_requires_nonempty_routes():
    payload = {
        "headline": OVERVIEW_PAYLOAD["headline"],
        "top_delayed": {"routes": [], "delayed_count": 0},
    }
    with pytest.raises(KeyError):
        render_template("overview_top_delay_route", {}, payload, "ja")
