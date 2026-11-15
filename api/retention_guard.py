"""A live read of `updates` refuses a range that starts before the agency's
retention floor (:func:`pipeline.retention.retention_floor`).

Below the floor `updates` no longer holds every row, so a live scan there
would report a dropped month as a month with no data, and one reaching across
the floor would give a partial answer. The aggregates keep every date, so only
the readers that scan `updates` refuse: :func:`pipeline.live_filing.filing`,
which every such read goes through, calls :func:`require_retained` first.

The answer is a 422 naming the floor (:func:`range_not_retained_handler`), and
the Ask tools give it as their usual refusal instead.
"""

from __future__ import annotations

import asyncio
import os
import time
from datetime import date

import psycopg2
import psycopg2.errors
from fastapi import Request
from fastapi.responses import JSONResponse

from pipeline import retention
from pipeline.cache import _REGISTERED_CLEARS
from pipeline.db import DEFAULT_DATABASE_URL

RANGE_NOT_RETAINED_CODE = "range_not_retained"

#: How long an agency's floor is trusted. A prune runs in another process and
#: only raises a floor, so a reader sees a new one within this window, while
#: a request costs no Postgres read at all inside it.
FLOOR_CACHE_TTL_SECONDS = 30.0

_CONNECT_TIMEOUT_SECONDS = 2
_STATEMENT_TIMEOUT_MS = 5000

_floors: dict[int, tuple[float, date | None]] = {}


class RangeNotRetained(Exception):
    """A live read of `updates` reaches before *floor*, the agency's retention floor."""

    def __init__(self, floor: date) -> None:
        super().__init__(f"raw readings before {floor.isoformat()} are no longer retained")
        self.floor = floor


def reset_floor_cache() -> None:
    _floors.clear()


_REGISTERED_CLEARS.append(reset_floor_cache)


def _read_floor(agency_id: int) -> date | None:
    """retention_floor on a short connection of its own: it takes a DB-API
    connection, and the API holds asyncpg ones. A database not yet migrated to
    hold the drops has had none, so it has no floor."""
    conn = psycopg2.connect(
        os.environ.get("DATABASE_URL") or DEFAULT_DATABASE_URL, connect_timeout=_CONNECT_TIMEOUT_SECONDS
    )
    try:
        conn.set_session(readonly=True)
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout = %s", (_STATEMENT_TIMEOUT_MS,))
        return retention.retention_floor(conn, agency_id)
    except psycopg2.errors.UndefinedTable:
        return None
    finally:
        conn.close()


async def cached_retention_floor(agency_id: int) -> date | None:
    """The agency's retention floor, read at most once per
    ``FLOOR_CACHE_TTL_SECONDS`` in this process."""
    now = time.monotonic()
    cached = _floors.get(agency_id)
    if cached is not None and now < cached[0]:
        return cached[1]
    floor = await asyncio.to_thread(_read_floor, agency_id)
    _floors[agency_id] = (now + FLOOR_CACHE_TTL_SECONDS, floor)
    return floor


async def require_retained(agency_id: int, first: date) -> None:
    """Raise :class:`RangeNotRetained` when a live read of `updates` from the
    service date *first* would start before the agency's retention floor."""
    floor = await cached_retention_floor(agency_id)
    if floor is not None and first < floor:
        raise RangeNotRetained(floor)


async def range_not_retained_handler(request: Request, exc: Exception) -> JSONResponse:
    """Answer :class:`RangeNotRetained` with a localized 422 that names the floor."""
    # pipeline.query.tools reaches this module through pipeline.live_filing,
    # so its strings are read here rather than at import.
    from api.deps import get_locale
    from pipeline.query.tools import _summary

    assert isinstance(exc, RangeNotRetained)
    floor = exc.floor.isoformat()
    detail = _summary(RANGE_NOT_RETAINED_CODE, lang=get_locale(request), floor=floor)
    return JSONResponse(status_code=422, content={"detail": detail, "code": RANGE_NOT_RETAINED_CODE, "floor": floor})
