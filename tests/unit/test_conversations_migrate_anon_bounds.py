"""``api.routers.conversations.MigrateAnon``: the anon-to-account
migration body is entirely client-supplied localStorage content, so it needs
the same kind of bound a paginated list gets server-side -- capped at 100
threads, each thread capped at 500 messages, all messages together capped at
the browser storage they came from, and ``AnonThread.title`` capped at 200
chars to mirror ``CreateConversation.title``.
"""

import pytest
from pydantic import ValidationError

from api.routers.conversations import _MAX_ANON_MESSAGES_CHARS, _MAX_FILTER_CTX_BYTES, AnonThread, MigrateAnon


def _thread(**overrides) -> dict:
    base = dict(
        client_id="c1",
        agency_id=1,
        title="a thread",
        filter_ctx={},
        pinned=False,
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
        messages=[],
    )
    base.update(overrides)
    return base


def test_anon_thread_accepts_title_at_max_length():
    AnonThread(**_thread(title="a" * 200))


def test_anon_thread_rejects_title_over_max_length():
    with pytest.raises(ValidationError):
        AnonThread(**_thread(title="a" * 201))


def test_anon_thread_rejects_more_than_500_messages():
    with pytest.raises(ValidationError):
        AnonThread(**_thread(messages=[{"role": "user", "content": "hi"}] * 501))


def test_anon_thread_accepts_exactly_500_messages():
    AnonThread(**_thread(messages=[{"role": "user", "content": "hi"}] * 500))


def test_migrate_anon_rejects_more_than_100_threads():
    with pytest.raises(ValidationError):
        MigrateAnon(threads=[_thread(client_id=str(i)) for i in range(101)])


def test_migrate_anon_accepts_exactly_100_threads():
    MigrateAnon(threads=[_thread(client_id=str(i)) for i in range(100)])


def test_migrate_anon_bounds_the_messages_of_all_threads_together():
    """Every message is stored, so the migration as a whole is capped at what
    the browser's own copy could hold, even when no one thread is large."""
    half = [{"role": "user", "rendered_summary": "x" * (_MAX_ANON_MESSAGES_CHARS // 2)}]
    MigrateAnon(threads=[_thread(client_id="a", messages=half)])
    with pytest.raises(ValidationError):
        MigrateAnon(threads=[_thread(client_id=str(i), messages=half) for i in range(2)])


def test_anon_thread_rejects_oversized_filter_ctx():
    """`filter_ctx` is client-supplied and persisted as jsonb, so it needs the
    same ceiling as the sibling fields on this model."""
    with pytest.raises(ValidationError):
        AnonThread(
            id="t1",
            title="t",
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
            filter_ctx={"blob": "x" * (_MAX_FILTER_CTX_BYTES + 1)},
        )
