"""Decision table for `scripts/check_hash_token_cleanup.py`.

A count of ``None`` means that raw column is already gone. The counting
queries themselves run against real rows in
`tests/scripts/test_check_hash_token_cleanup.py`.
"""

from __future__ import annotations

import pytest

from scripts.check_hash_token_cleanup import CleanupState, cleanup_is_due


@pytest.mark.parametrize(
    ("live_raw_sessions", "live_raw_api_keys", "due"),
    [
        (0, 0, True),
        (1, 0, False),
        (0, 1, False),
        (4, 2, False),
        (None, 0, True),
        (0, None, True),
        (None, 3, False),
        (5, None, False),
        (None, None, False),
    ],
)
def test_cleanup_is_due(live_raw_sessions: int | None, live_raw_api_keys: int | None, due: bool) -> None:
    state = CleanupState(live_raw_sessions=live_raw_sessions, live_raw_api_keys=live_raw_api_keys)
    assert cleanup_is_due(state) is due
