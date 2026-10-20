"""When the follow-up faithfulness gate skips rather than fails.

The follow-up only uses the providers ``ASK_FOLLOWUP_PROVIDERS`` allows, so a
key held only by a provider outside that list leaves nothing to probe. That is
a skipped gate (exit 2), not a regression (exit 1).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import pipeline.query.followup as followup
import pipeline.query.llm_client as llm_client
from scripts import followup_eval


def _client_with(*names: str) -> SimpleNamespace:
    return SimpleNamespace(providers=lambda: [SimpleNamespace(name=name) for name in names])


async def _never_called(**_kwargs: object) -> tuple[str, None]:
    raise AssertionError("no probe may be sent when no allowed provider is configured")


@pytest.fixture(autouse=True)
def _no_env_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(followup_eval, "_load_env", lambda: None)
    monkeypatch.setattr(followup, "answer_followup", _never_called)


def test_skips_when_only_a_provider_outside_the_allow_list_has_a_key(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.delenv("ASK_FOLLOWUP_PROVIDERS", raising=False)
    monkeypatch.setattr(llm_client, "get_client", lambda: _client_with("groq"))

    assert asyncio.run(followup_eval.main()) == 2
    assert "gemini" in capsys.readouterr().err


def test_skips_when_the_candidate_under_evaluation_has_no_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ASK_FOLLOWUP_PROVIDERS", "mistral")
    monkeypatch.setattr(llm_client, "get_client", lambda: _client_with("gemini", "openai"))

    assert asyncio.run(followup_eval.main()) == 2


def test_skips_when_no_provider_is_configured_at_all(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("ASK_FOLLOWUP_PROVIDERS", raising=False)
    monkeypatch.setattr(llm_client, "get_client", lambda: _client_with())

    assert asyncio.run(followup_eval.main()) == 2
