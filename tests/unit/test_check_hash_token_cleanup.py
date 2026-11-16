"""Decision table for `scripts/check_hash_token_cleanup.py`.

A count of ``None`` means the raw column is already gone. The counting query
itself runs against real rows in `tests/scripts/test_check_hash_token_cleanup.py`.
"""

from __future__ import annotations

import pytest

from scripts.check_hash_token_cleanup import cleanup_is_due, due_message


@pytest.mark.parametrize(("live_raw_sessions", "due"), [(0, True), (1, False), (4, False), (None, False)])
def test_cleanup_is_due(live_raw_sessions: int | None, due: bool) -> None:
    assert cleanup_is_due(live_raw_sessions) is due


def test_due_message_names_the_compat_objects_to_drop():
    message = due_message(["trigger sessions_fill_sid_hash", "index idx_sessions_sid"])
    assert "trigger sessions_fill_sid_hash, index idx_sessions_sid" in message
    assert "0062_hash_tokens_legacy_compat" in message


def test_due_message_without_compat_objects_mentions_none():
    assert "0062" not in due_message([])
