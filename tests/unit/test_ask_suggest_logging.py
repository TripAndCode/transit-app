"""Unit tests for ask_suggest's swallowed-failure logging.

ask_suggest is autocomplete: a failed embed or nearest-neighbour lookup must
still degrade to an empty suggestion list (never a 500 on every keystroke),
but at ``debug`` level the failure was invisible in production logs. These
call the route's raw function (bypassing slowapi's Request check via
``__wrapped__``) with mocked embedder/rag_nearest, no DB.
"""

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import api.routers.ask as ask_mod


async def test_ask_suggest_logs_embedding_failure_at_warning(caplog, monkeypatch):
    embedder = SimpleNamespace(available=True, embed=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(ask_mod, "get_embedder", lambda: embedder)

    endpoint = ask_mod.ask_suggest.__wrapped__
    with caplog.at_level(logging.WARNING, logger="api.routers.ask"):
        result = await endpoint(request=None, agency_id=1, conn=None, q="delay", limit=8)

    assert result.rows == [], "a failed lookup must still degrade to an empty suggestion envelope"
    records = [r for r in caplog.records if r.name == "api.routers.ask"]
    assert records, "expected a warning log from ask_suggest on embedding failure"
    assert any(r.exc_info for r in records), "expected exc_info=True so the traceback is captured"


async def test_ask_suggest_logs_rag_nearest_failure_at_warning(caplog, monkeypatch):
    embedder = SimpleNamespace(available=True, embed=lambda *a, **k: [0.1, 0.2])
    monkeypatch.setattr(ask_mod, "get_embedder", lambda: embedder)
    monkeypatch.setattr(ask_mod, "rag_nearest", AsyncMock(side_effect=RuntimeError("boom")))

    endpoint = ask_mod.ask_suggest.__wrapped__
    with caplog.at_level(logging.WARNING, logger="api.routers.ask"):
        result = await endpoint(request=None, agency_id=1, conn=None, q="delay", limit=8)

    assert result.rows == [], "a failed lookup must still degrade to an empty suggestion envelope"
    records = [r for r in caplog.records if r.name == "api.routers.ask"]
    assert records, "expected a warning log from ask_suggest on rag_nearest failure"
    assert any(r.exc_info for r in records), "expected exc_info=True so the traceback is captured"


async def test_repeat_failures_do_not_warn_on_every_keystroke(caplog, monkeypatch):
    """Autocomplete runs per keystroke, so a persistently broken embedder
    would otherwise emit a traceback per request and bury every other
    warning. The first failure warns; the repeats drop to debug."""
    ask_mod._SUGGEST_FAILING.clear()
    embedder = SimpleNamespace(available=True, embed=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(ask_mod, "get_embedder", lambda: embedder)
    endpoint = ask_mod.ask_suggest.__wrapped__

    with caplog.at_level(logging.DEBUG, logger="api.routers.ask"):
        for _ in range(5):
            await endpoint(request=None, agency_id=1, conn=None, q="delay", limit=8)

    records = [r for r in caplog.records if r.name == "api.routers.ask"]
    warnings = [r for r in records if r.levelno == logging.WARNING]
    assert len(warnings) == 1, f"expected one warning across five keystrokes, got {len(warnings)}"
    assert len(records) == 5, "every failure should still be recorded, just not all at warning"
    assert all(r.exc_info for r in records), "the traceback stays attached at both levels"


async def test_recovery_rearms_the_warning(caplog, monkeypatch):
    """A later outage must announce itself rather than staying silent
    because an earlier one already warned."""
    ask_mod._SUGGEST_FAILING.clear()
    failing = SimpleNamespace(available=True, embed=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(ask_mod, "get_embedder", lambda: failing)
    endpoint = ask_mod.ask_suggest.__wrapped__
    await endpoint(request=None, agency_id=1, conn=None, q="delay", limit=8)

    # Recover: a successful call clears the suppression.
    monkeypatch.setattr(ask_mod, "get_embedder", lambda: SimpleNamespace(available=True, embed=lambda *a, **k: [0.1]))
    monkeypatch.setattr(ask_mod, "rag_nearest", AsyncMock(return_value=[]))
    await endpoint(request=None, agency_id=1, conn=None, q="delay", limit=8)

    monkeypatch.setattr(ask_mod, "get_embedder", lambda: failing)
    caplog.clear()  # only the post-recovery outage is under test here
    with caplog.at_level(logging.DEBUG, logger="api.routers.ask"):
        await endpoint(request=None, agency_id=1, conn=None, q="delay", limit=8)

    warnings = [r for r in caplog.records if r.name == "api.routers.ask" and r.levelno == logging.WARNING]
    assert len(warnings) == 1, "the second outage should warn again after a recovery"
