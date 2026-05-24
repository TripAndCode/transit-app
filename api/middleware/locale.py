"""Accept-Language → ``request.state.locale``.

Tiny middleware that parses the inbound ``Accept-Language`` header and
pins one of the supported locales (``"ja"`` or ``"en"``) onto request
state. Downstream code (the Ask LLM prelude, tool summaries, the
reports formatter) reads this to render its output in the user's UI
language.

Defaults to ``"ja"`` when the header is absent or carries only locales
we don't support, so existing JP-only clients (and the entire pre-i18n
test suite) keep observing the previous behaviour.
"""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

SUPPORTED_LOCALES = ("ja", "en")
DEFAULT_LOCALE = "ja"


def _pick_locale(header: str | None) -> str:
    """Parse ``Accept-Language`` and return the best supported match.

    Honours q-values: ranges with explicit ``q=0`` are skipped, and the
    remaining tags are walked in descending q order (stable on ties).
    Matches by primary subtag (``en-US`` → ``en``), so a browser sending
    ``en-GB,en;q=0.9,ja;q=0.5`` still picks ``en``. Falls back to
    :data:`DEFAULT_LOCALE` when nothing supported is offered.
    """
    if not header:
        return DEFAULT_LOCALE
    candidates: list[tuple[float, int, str]] = []
    for idx, raw in enumerate(header.split(",")):
        part = raw.strip()
        if not part:
            continue
        tag, _, params = part.partition(";")
        tag = tag.strip().lower()
        if not tag or tag == "*":
            continue
        q = 1.0
        for p in params.split(";"):
            p = p.strip()
            if p.startswith("q="):
                try:
                    q = float(p[2:])
                except ValueError:
                    q = 0.0
        if q <= 0:
            continue
        primary = tag.split("-", 1)[0]
        if primary in SUPPORTED_LOCALES:
            # Negate idx so a stable sort by (-q, idx) keeps source order
            # on ties.
            candidates.append((q, -idx, primary))
    if not candidates:
        return DEFAULT_LOCALE
    candidates.sort(reverse=True)
    return candidates[0][2]


class LocaleMiddleware(BaseHTTPMiddleware):
    """Assign ``request.state.locale`` from the ``Accept-Language`` header."""

    async def dispatch(self, request: Request, call_next):
        request.state.locale = _pick_locale(request.headers.get("accept-language"))
        return await call_next(request)
