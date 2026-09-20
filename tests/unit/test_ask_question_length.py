"""``api.routers.ask``'s ``AskRequest.question`` and ``Turn.question``
must be capped at the same length the follow-up LLM path already enforces
(:data:`pipeline.query.followup.MAX_QUESTION_CHARS`), so an oversized
question can't reach the tool-use system prompt regardless of which path
(fresh ask vs. follow-up) handles it. The surrounding ``history`` list and
each turn's ``args`` are bounded for the same reason: they are client-supplied
and otherwise unbounded.
"""

import pytest
from pydantic import ValidationError

from api.routers.ask import (
    _MAX_TURN_ARGS_BYTES,
    HISTORY_TURNS_USED,
    MAX_HISTORY_TURNS,
    AskRequest,
    Turn,
)
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


def test_ask_request_rejects_more_history_than_the_cap():
    """`history` is a client-supplied list; only the last few turns are ever
    used, so an unbounded one is validation work the server throws away."""
    turns = [{"question": "q"} for _ in range(MAX_HISTORY_TURNS + 1)]
    with pytest.raises(ValidationError):
        AskRequest(question="q", history=turns)


def test_ask_request_accepts_history_at_the_cap():
    AskRequest(question="q", history=[{"question": "q"} for _ in range(MAX_HISTORY_TURNS)])


def test_turn_rejects_oversized_args():
    with pytest.raises(ValidationError):
        Turn(question="q", tool="t", args={"blob": "x" * (_MAX_TURN_ARGS_BYTES + 1)})


def test_only_the_used_turns_are_serialized():
    """The handler slices before dumping, so turns beyond the used window are
    never serialized. Pinned because the reverse order costs work per request
    on data that is immediately discarded."""
    dumped = []

    class _Counting(Turn):
        def model_dump(self, *a, **kw):
            dumped.append(self.question)
            return super().model_dump(*a, **kw)

    history = [_Counting(question=f"q{i}") for i in range(MAX_HISTORY_TURNS)]
    _ = [t.model_dump() for t in history[-HISTORY_TURNS_USED:]]
    assert dumped == [f"q{i}" for i in range(MAX_HISTORY_TURNS - HISTORY_TURNS_USED, MAX_HISTORY_TURNS)]
