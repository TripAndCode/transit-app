"""Backend logging config + request-id contextvar.

`configure()` installs a key-value formatter on the root logger; every
existing `logging.getLogger(__name__)` user picks up the new format
without code changes. `_RequestIdFilter` injects the current request
id (or '-') into every LogRecord so the format string can reference
`%(request_id)s` unconditionally.

Idempotent: a second `configure()` call clears existing handlers
before installing a fresh one, so test fixtures + app startup can
both call it without duplicating output.
"""

import logging
import os
import time
from contextvars import ContextVar

REQUEST_ID_CTX: ContextVar[str] = ContextVar("request_id", default="-")


class _RequestIdFilter(logging.Filter):
    """Inject the current request_id (or '-') into every LogRecord."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = REQUEST_ID_CTX.get()
        return True


def configure(level: str | None = None) -> None:
    """Install the key-value formatter on the root logger.

    `level` defaults to the LOG_LEVEL env var, then 'INFO'. Clears
    existing handlers before installing the new one so re-entry is
    safe.
    """
    resolved = (level or os.environ.get("LOG_LEVEL") or "INFO").upper()
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = logging.StreamHandler()
    handler.addFilter(_RequestIdFilter())
    fmt = '%(asctime)s.%(msecs)03dZ %(levelname)s %(name)s request_id=%(request_id)s msg="%(message)s"'
    formatter = logging.Formatter(fmt=fmt, datefmt="%Y-%m-%dT%H:%M:%S")
    formatter.converter = time.gmtime
    handler.setFormatter(formatter)
    root.addHandler(handler)
    root.setLevel(resolved)
