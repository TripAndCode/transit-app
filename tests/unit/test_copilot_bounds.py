"""``api.routers.copilot.CopilotInsightRequest`` (B11): ``filters`` and
``view_payload`` are arbitrary client-supplied dicts threaded into the
proactive-insight template; each is rejected once its JSON serialization
exceeds 16384 bytes.
"""

import pytest
from pydantic import ValidationError

from api.routers.copilot import CopilotInsightRequest


def test_copilot_insight_request_accepts_small_payloads():
    CopilotInsightRequest(tab="overview", filters={"a": 1}, view_payload={"b": 2})


def test_copilot_insight_request_rejects_oversized_filters():
    with pytest.raises(ValidationError):
        CopilotInsightRequest(tab="overview", filters={"x": "a" * 20000}, view_payload={})


def test_copilot_insight_request_rejects_oversized_view_payload():
    with pytest.raises(ValidationError):
        CopilotInsightRequest(tab="overview", filters={}, view_payload={"x": "a" * 20000})
