"""Daily usage counts, buffered in process and written in batches.

Two tables share this module: ``user_activity_daily`` (a signed-in user,
keyed by ``ActivityKey``) and ``anonymous_activity_daily`` (everyone else,
keyed by ``AnonymousKey`` and holding no identity at all). ``via_api_key``
on ``user_activity_daily`` is always written ``false``: part of the table's
unique key until it is dropped, but no longer a distinction this app draws.
``ActivityBuffer.record`` runs on the request path, so it only bumps an
in-memory counter; ``flush``/``flush_anonymous`` turn the accumulated counts
into one additive upsert per table. Additive, so several workers or replicas
flushing the same key simply sum. A flush that fails logs and drops its
batch rather than keeping it: no request ever waits on or fails because of
this bookkeeping, a database outage cannot grow a buffer without bound, and
one table's failure never blocks the other's flush. Counts since the last
flush are lost on a crash, which is acceptable for usage analytics and is
why this is not a billing meter.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Generic, TypeVar
from zoneinfo import ZoneInfo

_log = logging.getLogger(__name__)

_JST = ZoneInfo("Asia/Tokyo")
FLUSH_INTERVAL_SECONDS = 30.0

# The path segment is the caller's raw string, signed in or not, and one bad
# key fails the whole batch's upsert. asyncpg encodes an ``int[]`` parameter
# as ``int4`` and raises on a value outside it; and 0 is the ``COALESCE``
# sentinel both tables' unique keys use for "no agency", so a 0 and a NULL
# for the same route in one batch would hit the same row twice. Agency ids
# start at 1, so anything outside 1..int4 max counts as no agency.
_MAX_PG_INT = 2**31 - 1


@dataclass(frozen=True)
class ActivityKey:
    user_id: int
    day: date
    route: str
    method: str
    agency_id: int | None


@dataclass(frozen=True)
class AnonymousKey:
    day: date
    route: str
    method: str
    agency_id: int | None


_K = TypeVar("_K")


class ActivityBuffer(Generic[_K]):
    def __init__(self) -> None:
        self._counts: dict[_K, list[int]] = {}

    def record(self, key: _K, *, is_error: bool) -> None:
        counts = self._counts.setdefault(key, [0, 0])
        counts[0] += 1
        if is_error:
            counts[1] += 1

    def drain(self) -> dict[_K, list[int]]:
        batch, self._counts = self._counts, {}
        return batch


BUFFER: ActivityBuffer[ActivityKey] = ActivityBuffer()
ANONYMOUS_BUFFER: ActivityBuffer[AnonymousKey] = ActivityBuffer()


def today_jst() -> date:
    return datetime.now(_JST).date()


def agency_id_from(path_params: dict | None) -> int | None:
    raw = (path_params or {}).get("agency_id")
    if raw is None:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    if not (1 <= value <= _MAX_PG_INT):
        return None
    return value


# Rows whose user was deleted since they were counted are skipped rather than
# left to fail the foreign key, which would drop every other user's counts in
# the same batch.
_USER_UPSERT_SQL = """
    INSERT INTO user_activity_daily (user_id, day, route, method, agency_id, via_api_key, requests, errors)
    SELECT u.user_id, u.day, u.route, u.method, u.agency_id, false, u.requests, u.errors
    FROM unnest($1::int[], $2::date[], $3::text[], $4::text[], $5::int[], $6::int[], $7::int[])
        AS u(user_id, day, route, method, agency_id, requests, errors)
    WHERE EXISTS (SELECT 1 FROM users WHERE users.user_id = u.user_id)
    ON CONFLICT (user_id, day, route, method, (COALESCE(agency_id, 0)), via_api_key) DO UPDATE SET
        requests = user_activity_daily.requests + EXCLUDED.requests,
        errors   = user_activity_daily.errors + EXCLUDED.errors
"""

_ANONYMOUS_UPSERT_SQL = """
    INSERT INTO anonymous_activity_daily (day, route, method, agency_id, requests, errors)
    SELECT a.day, a.route, a.method, a.agency_id, a.requests, a.errors
    FROM unnest($1::date[], $2::text[], $3::text[], $4::int[], $5::int[], $6::int[])
        AS a(day, route, method, agency_id, requests, errors)
    ON CONFLICT (day, route, method, (COALESCE(agency_id, 0))) DO UPDATE SET
        requests = anonymous_activity_daily.requests + EXCLUDED.requests,
        errors   = anonymous_activity_daily.errors + EXCLUDED.errors
"""


async def flush(buffer: ActivityBuffer[ActivityKey], pool: Any) -> int:
    """Write and forget ``buffer``'s counts; return how many keys were sent."""
    batch = buffer.drain()
    if not batch:
        return 0
    keys = list(batch)
    try:
        await pool.execute(
            _USER_UPSERT_SQL,
            [k.user_id for k in keys],
            [k.day for k in keys],
            [k.route for k in keys],
            [k.method for k in keys],
            [k.agency_id for k in keys],
            [batch[k][0] for k in keys],
            [batch[k][1] for k in keys],
        )
    except Exception:
        _log.warning("Dropped %d usage count(s): the flush to user_activity_daily failed", len(keys), exc_info=True)
        return 0
    return len(keys)


async def flush_anonymous(buffer: ActivityBuffer[AnonymousKey], pool: Any) -> int:
    """Write and forget ``buffer``'s counts; return how many keys were sent."""
    batch = buffer.drain()
    if not batch:
        return 0
    keys = list(batch)
    try:
        await pool.execute(
            _ANONYMOUS_UPSERT_SQL,
            [k.day for k in keys],
            [k.route for k in keys],
            [k.method for k in keys],
            [k.agency_id for k in keys],
            [batch[k][0] for k in keys],
            [batch[k][1] for k in keys],
        )
    except Exception:
        _log.warning(
            "Dropped %d usage count(s): the flush to anonymous_activity_daily failed", len(keys), exc_info=True
        )
        return 0
    return len(keys)


async def _flush_periodically(app: Any) -> None:
    while True:
        await asyncio.sleep(FLUSH_INTERVAL_SECONDS)
        # Shielded: the batch leaves the buffer before the write starts, so a
        # shutdown's cancel landing mid-write would lose it. The write runs to
        # completion on its own; the pool's close waits for its connection.
        await asyncio.shield(flush_anonymous(ANONYMOUS_BUFFER, app.state.pool))
        await asyncio.shield(flush(BUFFER, app.state.pool))


def start(app: Any) -> None:
    app.state.activity_flusher = asyncio.create_task(_flush_periodically(app))


async def stop(app: Any) -> None:
    """Stop the periodic flush, then write what is left while the pool is still open."""
    task = getattr(app.state, "activity_flusher", None)
    if task is not None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    pool = getattr(app.state, "pool", None)
    if pool is not None:
        await flush_anonymous(ANONYMOUS_BUFFER, pool)
        await flush(BUFFER, pool)
