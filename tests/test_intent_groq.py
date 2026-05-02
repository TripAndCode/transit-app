import json
from unittest.mock import MagicMock, patch

import pytest

from pipeline.query.intent import _reset_groq_client, classify_intent


@pytest.fixture(autouse=True)
def reset_client():
    _reset_groq_client()
    yield
    _reset_groq_client()


def _make_mock_client(content: str):
    mock_msg = MagicMock()
    mock_msg.content = content
    mock_choice = MagicMock()
    mock_choice.message = mock_msg
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = mock_response
    return mock_client


@pytest.mark.asyncio
async def test_classify_intent_live_delays():
    mock_client = _make_mock_client(
        json.dumps(
            {
                "query_type": "ranking",
                "route": None,
                "route_name": None,
                "service": None,
                "dow": None,
                "dow_group": None,
                "date": None,
                "stop_name": None,
                "time_band": None,
                "trend_direction": "any",
                "compare_polarity": "any",
                "sort_order": "desc",
                "limit": 10,
                "unknown": False,
            }
        )
    )
    with patch("pipeline.query.intent._get_groq_client", return_value=mock_client):
        result = await classify_intent("今走っているバスは？")
    assert result["query_type"] == "ranking"


@pytest.mark.asyncio
async def test_classify_intent_unknown():
    mock_client = _make_mock_client(
        json.dumps(
            {
                "query_type": "unknown",
                "route": None,
                "route_name": None,
                "service": None,
                "dow": None,
                "dow_group": None,
                "date": None,
                "stop_name": None,
                "time_band": None,
                "trend_direction": "any",
                "compare_polarity": "any",
                "sort_order": "desc",
                "limit": 10,
                "unknown": True,
            }
        )
    )
    with patch("pipeline.query.intent._get_groq_client", return_value=mock_client):
        result = await classify_intent("天気は？")
    assert result["unknown"] is True
