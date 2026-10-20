"""The follow-up's provider allow-list, read from ``ASK_FOLLOWUP_PROVIDERS``."""

from __future__ import annotations

import pytest

from pipeline.query.followup import _allowed_providers


def test_unset_allows_the_verified_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ASK_FOLLOWUP_PROVIDERS", raising=False)
    assert _allowed_providers() == {"gemini", "openai"}


def test_empty_allows_no_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASK_FOLLOWUP_PROVIDERS", " , ")
    assert _allowed_providers() == set()
    monkeypatch.setenv("ASK_FOLLOWUP_PROVIDERS", "")
    assert _allowed_providers() == set()


def test_names_are_trimmed_and_lowercased(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASK_FOLLOWUP_PROVIDERS", " Gemini ,OPENAI")
    assert _allowed_providers() == {"gemini", "openai"}
