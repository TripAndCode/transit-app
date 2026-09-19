"""``api.routers.ask``'s ``AskRequest.question`` and ``Turn.question`` (B6)
must be capped at the same length the follow-up LLM path already enforces
(:data:`pipeline.query.followup.MAX_QUESTION_CHARS`), so an oversized
question can't reach the tool-use system prompt regardless of which path
(fresh ask vs. follow-up) handles it.
"""

import pytest
from pydantic import ValidationError

from api.routers.ask import AskRequest, Turn
from pipeline.query.followup import MAX_QUESTION_CHARS


def test_ask_request_accepts_question_at_max_length():
    AskRequest(question="a" * MAX_QUESTION_CHARS)


def test_ask_request_rejects_question_over_max_length():
    with pytest.raises(ValidationError):
        AskRequest(question="a" * (MAX_QUESTION_CHARS + 1))


def test_turn_accepts_question_at_max_length():
    Turn(question="a" * MAX_QUESTION_CHARS)


def test_turn_rejects_question_over_max_length():
    with pytest.raises(ValidationError):
        Turn(question="a" * (MAX_QUESTION_CHARS + 1))
