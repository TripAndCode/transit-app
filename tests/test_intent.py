import pytest

from pipeline.query.intent import validate_intent


# Override the session-scoped DB fixture so intent tests run without PostgreSQL
@pytest.fixture(scope="session", autouse=True)
def apply_schema():
    """No-op override: intent tests are pure Python, no DB needed."""
    yield


def test_validate_intent_valid():
    intent = {"query_type": "route_stats", "route": "49022", "unknown": False}
    # route_stats is not a valid query type — should be unknown
    result = validate_intent(intent)
    assert result["unknown"] is True


def test_validate_intent_ranking_valid():
    intent = {"query_type": "ranking", "unknown": False}
    result = validate_intent(intent)
    assert result["unknown"] is False
    assert result["query_type"] == "ranking"


def test_validate_intent_unknown_query_type():
    intent = {"query_type": "nonexistent", "unknown": False}
    result = validate_intent(intent)
    assert result["unknown"] is True


def test_validate_intent_route_non_digits():
    intent = {"query_type": "ranking", "route": "abc", "unknown": False}
    result = validate_intent(intent)
    # "abc" is not digits, not Japanese — route should be None but not unknown
    assert result["route"] is None
    assert result["unknown"] is False


def test_validate_intent_bad_date():
    intent = {"query_type": "by_date", "route": "49022", "date": "not-a-date", "unknown": False}
    result = validate_intent(intent)
    assert result["unknown"] is True  # by_date requires date


def test_validate_intent_by_hour_requires_route():
    intent = {"query_type": "by_hour", "unknown": False}
    result = validate_intent(intent)
    assert result["unknown"] is True


def test_validate_intent_dow_ranking_requires_dow():
    intent = {"query_type": "dow_ranking", "dow": None, "unknown": False}
    result = validate_intent(intent)
    assert result["unknown"] is True


def test_validate_intent_route_digits():
    intent = {"query_type": "ranking", "route": "49022", "unknown": False}
    result = validate_intent(intent)
    assert result["route"] == "49022"


def test_validate_intent_limit_clamped():
    intent = {"query_type": "ranking", "limit": 200, "unknown": False}
    result = validate_intent(intent)
    assert result["limit"] == 100


def test_validate_intent_time_band_japanese():
    intent = {"query_type": "ranking", "time_band": "朝", "unknown": False}
    result = validate_intent(intent)
    assert result["time_band"] == "morning"


# NOTE: the ollama-based classify_intent path was retired during v2 in
# favor of the Groq tool-use chat (pipeline/query/chat.py). The Groq path
# is covered by tests/test_intent_groq.py. The two ollama tests that
# lived here are gone with the dependency.


def test_validate_intent_routes_at_stop_requires_stop_name():
    from pipeline.query.intent import validate_intent

    result = validate_intent({"query_type": "routes_at_stop"})
    assert result["unknown"] is True


@pytest.mark.asyncio
async def test_aconn_connects(aconn):
    row = await aconn.fetchrow("SELECT 1 AS val")
    assert row["val"] == 1
