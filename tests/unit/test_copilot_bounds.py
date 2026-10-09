"""``api.routers.copilot.CopilotInsightRequest``: ``view_payload`` is an
arbitrary client-supplied dict threaded into the proactive-insight template,
rejected once its JSON serialization exceeds the size cap.
"""

import json
from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from api.routers.copilot import _MAX_PAYLOAD_BYTES, CopilotInsightRequest


def test_copilot_insight_request_accepts_small_payloads():
    CopilotInsightRequest(tab="overview", view_payload={"b": 2})


def test_a_client_that_still_sends_filters_is_accepted():
    request = CopilotInsightRequest.model_validate({"tab": "overview", "filters": {"a": 1}, "view_payload": {"b": 2}})
    assert "filters" not in request.model_dump()


# Derived from the cap rather than written as a literal, so raising the cap
# cannot silently turn these into tests that assert nothing.
_OVERSIZE = "a" * (_MAX_PAYLOAD_BYTES + 1)


def test_copilot_insight_request_rejects_oversized_view_payload():
    with pytest.raises(ValidationError):
        CopilotInsightRequest(tab="overview", view_payload={"x": _OVERSIZE})


def test_cap_admits_the_widest_payload_the_overview_tab_itself_posts():
    """A transport bound must not reject the application's own traffic.

    CopilotPanel posts the whole OverviewSummary, and its per-day fields grow
    with the selected range, so the worst legitimate case is a full
    MAX_RANGE_DAYS window — reconstructed here at that shape rather than
    asserted against a fixed byte count, which would drift with the payload.
    """
    from api.range import MAX_RANGE_DAYS

    day = date(2026, 1, 1)
    view_payload = {
        "service_split_daily": [
            {"date": (day + timedelta(days=i)).isoformat(), "service_type": st, "avg": 12.345678901}
            for i in range(MAX_RANGE_DAYS)
            for st in ("weekday", "weekend_holiday")
        ],
        "sparkline_points": [
            {"date": (day + timedelta(days=i)).isoformat(), "avg_min": 12.3456789, "samples": 123456}
            for i in range(MAX_RANGE_DAYS)
        ],
    }
    assert len(json.dumps(view_payload).encode()) < _MAX_PAYLOAD_BYTES

    CopilotInsightRequest(tab="overview", view_payload=view_payload)
