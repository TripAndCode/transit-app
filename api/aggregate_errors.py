"""Graceful handling of missing aggregate tables (migration-lagged deployments).

Every read endpoint serves from precomputed ``agg_*`` tables. On a deployment
whose schema is behind (e.g. the dev DB stuck at an old migration), a query hits
a table that doesn't exist yet and asyncpg raises ``UndefinedTableError`` — which
would otherwise surface as an opaque HTTP 500 / white screen.

Rather than 500, we answer **503** with a plain-language, localized message and a
machine ``code`` so clients can distinguish "this environment hasn't built the
data yet" from a genuine bug. The actionable detail (which relation is missing)
is logged server-side for ops — the migration-drift check (``make
check-migrations``) is the loud gate; this is the user-facing safety net.
"""

import logging

from fastapi import Request
from fastapi.responses import JSONResponse

from api.deps import get_locale

logger = logging.getLogger("api.aggregate")

AGGREGATE_NOT_READY_CODE = "aggregate_not_ready"

# Plain language, no internal table names (those go to the server log only).
_MESSAGE = {
    "ja": "この画面のデータはこの環境ではまだ準備されていません。しばらくしてから再度お試しください。",
    "en": "Data for this view hasn't been prepared in this environment yet. Please try again later.",
}


async def aggregate_not_ready_handler(request: Request, exc: Exception) -> JSONResponse:
    """Map a missing-aggregate ``UndefinedTableError`` to a localized 503."""
    # `str(exc)` is e.g. 'relation "agg_route_hour_dow" does not exist' — useful
    # for ops to know which migration/analyze is outstanding; never sent to the client.
    logger.error("Aggregate table missing (migrations/analyze behind?): %s", exc)
    locale = get_locale(request)
    detail = _MESSAGE.get(locale, _MESSAGE["ja"])
    return JSONResponse(status_code=503, content={"detail": detail, "code": AGGREGATE_NOT_READY_CODE})
