"""``generate_proactive_insight`` renders the template its payload selects.

The choice is made in code (see ``test_copilot_templates``), so no provider is
ever contacted and the same payload always yields the same insight.
"""

import pytest

from pipeline.query import copilot
from pipeline.query.llm_client import LLMClient

_ROUTE = {"route_code": "R12", "route_short_name": "12", "avg_min": 14.2}

OVERVIEW_PAYLOAD = {
    "headline": {"avg_min": 6.4, "baseline_avg_min": 4.1, "delta_min": 2.3, "delta_pct": 56.1, "samples": 812},
    "top_delayed": {"routes": [_ROUTE], "delayed_count": 1},
}


async def test_generating_an_insight_never_contacts_a_provider(monkeypatch):
    def _no_provider(self, **kwargs):
        raise AssertionError("the insight must not reach an LLM provider")

    monkeypatch.setattr(LLMClient, "chat_completions", _no_provider)
    result = await copilot.generate_proactive_insight("overview", OVERVIEW_PAYLOAD, locale="en")
    assert "14.2" in result["text"]


async def test_generate_proactive_insight_interpolates_from_payload():
    result = await copilot.generate_proactive_insight("overview", OVERVIEW_PAYLOAD, locale="en")
    assert "14.2" in result["text"]
    assert result["low_confidence"] is False
    assert "Overview" in result["cite"]


async def test_low_confidence_comes_from_the_payload():
    payload = {**OVERVIEW_PAYLOAD, "low_confidence": True}
    result = await copilot.generate_proactive_insight("overview", payload, locale="en")
    assert result["low_confidence"] is True


async def test_generate_proactive_insight_rejects_missing_payload():
    with pytest.raises(copilot.NoInsightAvailable):
        await copilot.generate_proactive_insight("overview", {}, locale="en")


async def test_generate_proactive_insight_rejects_unknown_tab():
    with pytest.raises(copilot.NoInsightAvailable):
        await copilot.generate_proactive_insight("not_a_tab", OVERVIEW_PAYLOAD, locale="en")


async def test_a_template_that_cannot_render_falls_back_to_no_signal():
    """The selection only looks at ``top_delayed``; a payload whose other
    fields the template needs are missing still yields an insight."""
    payload = {"top_delayed": OVERVIEW_PAYLOAD["top_delayed"]}
    result = await copilot.generate_proactive_insight("overview", payload, locale="en")
    assert result["text"].startswith("Nothing stands out")


async def test_generate_proactive_insight_renders_in_requested_locale():
    """locale must reach the template, not stop at the function signature."""
    ja = await copilot.generate_proactive_insight("overview", OVERVIEW_PAYLOAD, locale="ja")
    en = await copilot.generate_proactive_insight("overview", OVERVIEW_PAYLOAD, locale="en")
    assert "路線" in ja["text"]
    assert "Route" not in ja["text"]
    assert "Route" in en["text"]
    assert ja["cite"] != en["cite"]
    # The numbers are identical regardless of locale — they come from the payload.
    assert "14.2" in ja["text"] and "14.2" in en["text"]


async def test_no_signal_is_localized():
    payload = {**OVERVIEW_PAYLOAD, "top_delayed": {"routes": [], "delayed_count": 0}}
    ja = await copilot.generate_proactive_insight("overview", payload, locale="ja")
    en = await copilot.generate_proactive_insight("overview", payload, locale="en")
    assert "目立った" in ja["text"]
    assert en["text"].startswith("Nothing stands out")
