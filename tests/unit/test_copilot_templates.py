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


def test_overview_top_delay_route_interpolates_only_payload_numbers():
    rendered = render_template("overview_top_delay_route", {}, OVERVIEW_PAYLOAD)
    assert "14.2" in rendered["text"]
    assert "12" in rendered["text"]
    # every digit sequence in the rendered text must trace back to the payload
    import re

    payload_numbers = {"14.2", "12", "9.8", "7", "6.4", "4.1", "2.3", "56.1", "812", "6"}
    for match in re.findall(r"\d+\.?\d*", rendered["text"]):
        assert match in payload_numbers, f"unexplained number {match!r} in {rendered['text']!r}"


def test_no_signal_template_has_no_numbers():
    rendered = render_template(NO_SIGNAL_TEMPLATE_ID, {}, OVERVIEW_PAYLOAD)
    import re

    assert re.findall(r"\d", rendered["text"]) == []


def test_unknown_template_id_raises():
    with pytest.raises(KeyError):
        render_template("not_a_real_template", {}, OVERVIEW_PAYLOAD)


def test_overview_top_delay_route_requires_top_delayed_routes():
    with pytest.raises(KeyError):
        render_template("overview_top_delay_route", {}, {"headline": OVERVIEW_PAYLOAD["headline"]})
