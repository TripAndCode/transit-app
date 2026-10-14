"""How a static strategy's ``fetch`` reports that it failed.

``fetch`` returning None means the source answered and had nothing newer.
Anything else that keeps it from answering raises :class:`StaticFetchError`,
so a dead source is never recorded as an up-to-date one.
"""

import http
import http.client
import urllib.error

from pipeline.url_guard import FeedURLError, _redact_url, redact_urls_in_text

#: What a fetch through ``safe_urlopen`` raises when the request failed: an
#: HTTP error status or a network error (``URLError``, and the timeouts and
#: resets a body read raises outside it, are all ``OSError``), a response
#: ``http.client`` could not parse, or a URL the SSRF guard rejected.
FETCH_ERRORS: tuple[type[BaseException], ...] = (OSError, http.client.HTTPException, FeedURLError)


class StaticFetchError(RuntimeError):
    """A timetable fetch that failed, so it cannot say whether anything is newer.

    ``reason`` is one operator-readable line. The run card and the logs show
    it as is, so every URL in it is redacted when it is built.
    """

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def fetch_failed(url: str, exc: BaseException) -> StaticFetchError:
    """The :class:`StaticFetchError` for one of :data:`FETCH_ERRORS` raised fetching ``url``."""
    where = _redact_url(url)
    if isinstance(exc, urllib.error.HTTPError):
        try:
            status = f"{exc.code} {http.HTTPStatus(exc.code).phrase}"
        except ValueError:
            status = str(exc.code)
        return StaticFetchError(f"HTTP {status} from {where}")
    if isinstance(exc, FeedURLError):
        return StaticFetchError(f"{where} was rejected by the URL guard: {redact_urls_in_text(str(exc))}")
    cause = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    detail = str(cause)
    if isinstance(cause, BaseException):
        detail = f"{type(cause).__name__}: {detail}" if detail else type(cause).__name__
    return StaticFetchError(f"network error fetching {where} ({redact_urls_in_text(detail)})")
