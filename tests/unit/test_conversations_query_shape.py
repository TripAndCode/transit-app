"""Pure query-shape test for `pipeline.query.conversations.get_message`:
asserts it issues a single-row lookup (`WHERE conversation_id = $1 AND
message_id = $2`) rather than fetching the whole thread and filtering in
Python. No DB needed -- inspects the function's own source text.
"""

from __future__ import annotations

import inspect

from pipeline.query.conversations import get_message


def test_get_message_is_a_single_row_lookup():
    src = inspect.getsource(get_message)
    assert "WHERE conversation_id = $1 AND message_id = $2" in src


def test_get_message_does_not_order_or_scan_the_whole_thread():
    src = inspect.getsource(get_message)
    assert "ORDER BY message_id" not in src
    assert "conn.fetch(" not in src  # fetchrow (single row), not fetch (all rows)
