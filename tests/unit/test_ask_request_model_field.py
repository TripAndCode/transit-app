"""``AskRequest`` has no ``model`` field.

Each rung of the provider ladder runs its own configured model, so a
client-chosen model name could be valid on at most one of them. A request
that still sends ``model`` is accepted with the field dropped, not rejected.
"""

from api.routers.ask import AskRequest


def test_a_client_supplied_model_is_accepted_and_dropped():
    req = AskRequest.model_validate({"question": "一番遅れている路線は？", "model": "gpt-4o"})
    assert req.question == "一番遅れている路線は？"
    assert "model" not in req.model_dump()
