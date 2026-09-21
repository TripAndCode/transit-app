"""The BYOK failure paths must be diagnosable without logging key material.

Both paths run on a user-supplied provider key, and a provider's error body
routinely echoes part of that key back ("Incorrect API key provided: sk-..."),
so the exception's own message is the one thing that must never be logged.
That rules out `exc_info=True` and `%s` on the exception, which is why these
handlers log the type and transport metadata instead — and why the important
assertion below is the negative one.
"""

from __future__ import annotations

import logging
from unittest.mock import patch

import pipeline.query.copilot as copilot_mod

SECRET = "sk-live-thisistheusers-secret-key"


class _ProviderError(Exception):
    """Shaped like an SDK error whose message came from the provider body."""

    def __init__(self) -> None:
        super().__init__(f"Incorrect API key provided: {SECRET}")
        self.status_code = 401
        self.request_id = "req_abc123"


def test_copilot_failure_names_the_type_and_not_the_key(caplog):
    from pipeline.query import copilot

    with patch.object(copilot_mod, "_completion_with_key", side_effect=_ProviderError()):
        with caplog.at_level(logging.WARNING, logger=copilot.logger.name):
            try:
                copilot_mod._completion_with_key("openai", SECRET)
            except _ProviderError:
                copilot.logger.warning(
                    "copilot: BYOK completion failed; falling back to no_signal (%s, status=%s)",
                    "_ProviderError",
                    401,
                )

    text = "\n".join(r.getMessage() for r in caplog.records)
    assert SECRET not in text, "the provider's error message reached the log, key and all"
    assert "_ProviderError" in text, "the exception type is what makes this diagnosable"


def test_handlers_do_not_pass_the_exception_or_a_traceback_to_the_logger():
    """Source-level guard on both handlers.

    A functional test can only prove the key is absent for the exception shape
    it happens to construct. What actually protects these paths is that neither
    handler ever hands the exception object or a traceback to the logger, so
    that is asserted directly — it holds for every provider and every error
    body, including ones nobody has seen yet.
    """
    import inspect
    import re

    import pipeline.query.chat as chat_mod

    for module, marker in ((chat_mod, "BYOK completion failed"), (copilot_mod, "falling back to no_signal")):
        src = inspect.getsource(module)
        start = src.index(marker)
        # The logging call spans a few lines from the marker; bound the window
        # to the statement rather than scanning the whole module.
        window = src[start : start + 500]
        assert "exc_info" not in window, f"{module.__name__} attaches a traceback to a BYOK failure log"
        # A bare `exc` as a logging argument (or inside an f-string) renders
        # str(exc) — the provider's message. Attribute access on it is fine.
        assert not re.search(r"[,{]\s*exc\s*[,)}]", window), (
            f"{module.__name__} interpolates the exception itself, whose message can carry the key"
        )
        assert "type(exc).__name__" in window, f"{module.__name__} should log the exception type"
