import json
from types import SimpleNamespace

import pytest

from pipeline.query import copilot


def _fake_tool_choice_message(template_id: str):
    func = SimpleNamespace(name="pick_template", arguments=json.dumps({"template_id": template_id, "params": {}}))
    call = SimpleNamespace(function=func, id="call_1", type="function")
    return SimpleNamespace(content=None, tool_calls=[call])


class _FakeClient:
    def __init__(self, template_id: str):
        self._template_id = template_id

    def chat_completions(self, **kwargs):
        return _fake_tool_choice_message(self._template_id), None


OVERVIEW_PAYLOAD = {
    "headline": {"avg_min": 6.4, "baseline_avg_min": 4.1, "delta_min": 2.3, "delta_pct": 56.1, "samples": 812},
    "top_delayed": {"routes": [{"route_code": "R12", "route_short_name": "12", "avg_min": 14.2}], "delayed_count": 1},
}


@pytest.mark.asyncio
async def test_generate_proactive_insight_interpolates_from_payload(monkeypatch):
    monkeypatch.setattr(copilot, "_get_client", lambda: _FakeClient("overview_top_delay_route"))
    result = await copilot.generate_proactive_insight("overview", {}, OVERVIEW_PAYLOAD, locale="en")
    assert "14.2" in result["text"]
    assert result["low_confidence"] is False
    assert "Overview" in result["cite"]


@pytest.mark.asyncio
async def test_generate_proactive_insight_rejects_missing_payload():
    with pytest.raises(copilot.NoInsightAvailable):
        await copilot.generate_proactive_insight("overview", {}, {}, locale="en")


@pytest.mark.asyncio
async def test_generate_proactive_insight_rejects_unknown_tab():
    with pytest.raises(copilot.NoInsightAvailable):
        await copilot.generate_proactive_insight("not_a_tab", {}, OVERVIEW_PAYLOAD, locale="en")


@pytest.mark.asyncio
async def test_generate_proactive_insight_falls_back_when_llm_picks_bad_id(monkeypatch):
    monkeypatch.setattr(copilot, "_get_client", lambda: _FakeClient("not_a_real_template"))
    result = await copilot.generate_proactive_insight("overview", {}, OVERVIEW_PAYLOAD, locale="en")
    assert result["text"]  # falls back to the no-signal template, never crashes


@pytest.mark.asyncio
async def test_generate_proactive_insight_renders_in_requested_locale(monkeypatch):
    """locale must reach the template, not stop at the function signature."""
    monkeypatch.setattr(copilot, "_get_client", lambda: _FakeClient("overview_top_delay_route"))
    ja = await copilot.generate_proactive_insight("overview", {}, OVERVIEW_PAYLOAD, locale="ja")
    en = await copilot.generate_proactive_insight("overview", {}, OVERVIEW_PAYLOAD, locale="en")
    assert "路線" in ja["text"]
    assert "Route" not in ja["text"]
    assert "Route" in en["text"]
    assert ja["cite"] != en["cite"]
    # The numbers are identical regardless of locale — they come from the payload.
    assert "14.2" in ja["text"] and "14.2" in en["text"]


@pytest.mark.asyncio
async def test_no_signal_fallback_is_localized(monkeypatch):
    monkeypatch.setattr(copilot, "_get_client", lambda: _FakeClient("not_a_real_template"))
    ja = await copilot.generate_proactive_insight("overview", {}, OVERVIEW_PAYLOAD, locale="ja")
    en = await copilot.generate_proactive_insight("overview", {}, OVERVIEW_PAYLOAD, locale="en")
    assert "目立った" in ja["text"]
    assert en["text"].startswith("Nothing stands out")
