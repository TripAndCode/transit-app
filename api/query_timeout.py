"""A ClickHouse query that runs out its time budget answers 504, not 500.

``api.clickhouse`` caps every read at ``max_execution_time``. A report whose
scan cannot finish inside it fails the same way on every retry, so the client
needs to tell it apart from a transient server error: it should not retry,
and the reader should narrow the period or the routes instead. The response
carries a plain-language, localized ``detail`` and a machine ``code``; the
server's own message stays in the log. Any other ClickHouse error is
re-raised and stays a 500.
"""

import logging

from clickhouse_connect.driver.exceptions import DatabaseError
from fastapi import Request
from fastapi.responses import JSONResponse

from api.deps import get_locale
from pipeline.clickhouse import is_query_timeout
from pipeline.query.tools import _summary

logger = logging.getLogger("api.clickhouse")

QUERY_TIMEOUT_CODE = "query_timeout"


async def clickhouse_error_handler(request: Request, exc: DatabaseError) -> JSONResponse:
    """Map a timed-out ClickHouse read to a localized 504; re-raise anything else."""
    if not is_query_timeout(exc):
        raise exc
    logger.warning("ClickHouse query exceeded its time budget: %s", exc)
    detail = _summary(QUERY_TIMEOUT_CODE, lang=get_locale(request))
    return JSONResponse(status_code=504, content={"detail": detail, "code": QUERY_TIMEOUT_CODE})
