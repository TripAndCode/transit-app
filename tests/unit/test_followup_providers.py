"""The follow-up's provider allow-list, read from ``ASK_FOLLOWUP_PROVIDERS``.

The unset default is pinned in tests/query/test_llm_client.py.
"""

from __future__ import annotations

import pytest

from pipeline.query.followup import _allowed_providers


def test_empty_allows_no_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASK_FOLLOWUP_PROVIDERS", " , ")
    assert _allowed_providers() == set()
    monkeypatch.setenv("ASK_FOLLOWUP_PROVIDERS", "")
    assert _allowed_providers() == set()


def test_names_are_trimmed_and_lowercased(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASK_FOLLOWUP_PROVIDERS", " Gemini ,OPENAI")
    assert _allowed_providers() == {"gemini", "openai"}
