"""Pure query-shape tests for `pipeline.query.conversations`.

`get_message` must issue a single-row lookup rather than fetching the whole
thread and filtering in Python, and `list_conversations` must keep a predicate
the planner can index. No DB needed -- inspects the functions' own source text.
"""

from __future__ import annotations

import inspect

from pipeline.query.conversations import get_message, list_conversations


def test_get_message_is_a_single_row_lookup():
    src = inspect.getsource(get_message)
    assert "WHERE conversation_id = $1 AND message_id = $2" in src


def test_get_message_does_not_order_or_scan_the_whole_thread():
    src = inspect.getsource(get_message)
    assert "ORDER BY message_id" not in src
    assert "conn.fetch(" not in src  # fetchrow (single row), not fetch (all rows)


def test_list_conversations_keeps_an_indexable_predicate():
    """`IS NOT DISTINCT FROM $1` is not sargable: asyncpg binds user_id as a
    parameter, so the planner cannot use it as an index condition even when
    the runtime value is NULL, and the whole table is scanned and sorted.

    Guarded because the shape is the tempting one to write — it expresses the
    intent in a single branch — and reverting to it is silent: the query still
    returns the right rows, just via a sequential scan.
    """
    # Comments are stripped first: the rationale above the predicate names the
    # bad form on purpose, and matching that would defeat the check.
    src = inspect.getsource(list_conversations)
    code = "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith("#"))
    assert "IS NOT DISTINCT FROM" not in code
    assert "user_id = $1" in code
    assert "user_id IS NULL" in code
