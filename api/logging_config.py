"""Backend logging config + request-id contextvar.

`configure()` installs a key-value formatter on the root logger; every
existing `logging.getLogger(__name__)` user picks up the new format
without code changes. `_RequestIdFilter` injects the current request
id (or '-') into every LogRecord so the format string can reference
`%(request_id)s` unconditionally.

Idempotent: a second `configure()` call clears existing handlers
before installing a fresh one, so test fixtures + app startup can
both call it without duplicating output. `_RateLimitAddressFilter`
strips the client address slowapi logs on a 429, so server logs
never carry a client address; `addFilter` ignores a duplicate
instance of it, so re-running `configure()` doesn't attach it twice.
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


class _RateLimitAddressFilter(logging.Filter):
    """Strip the client address slowapi logs on every 429.

    Server logs never carry a client address: rate limiting counts requests
    per IP in memory and the address itself is never stored or printed.
    """

    _SLOWAPI_FORMAT = "ratelimit %s (%s) exceeded at endpoint: %s"

    def filter(self, record: logging.LogRecord) -> bool:
        if record.msg == self._SLOWAPI_FORMAT and isinstance(record.args, tuple) and len(record.args) == 3:
            limit, _limit_key, scope = record.args
            record.msg = "ratelimit %s exceeded at endpoint: %s"
            record.args = (limit, scope)
        return True


_rate_limit_address_filter = _RateLimitAddressFilter()


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
    logging.getLogger("slowapi").addFilter(_rate_limit_address_filter)
