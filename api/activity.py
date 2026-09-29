"""Per-user daily usage counts, buffered in process and written in batches.

``ActivityBuffer.record`` runs on the request path, so it only bumps an
in-memory counter; ``flush`` turns the accumulated counts into one additive
upsert into ``user_activity_daily``. Additive, so several workers or replicas
flushing the same key simply sum. A flush that fails logs and drops its
batch rather than keeping it: no request ever waits on or fails because of
this bookkeeping, and a database outage cannot grow the buffer without
bound. Counts since the last flush are lost on a crash, which is acceptable
for usage analytics and is why this is not a billing meter.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

_log = logging.getLogger(__name__)

_JST = ZoneInfo("Asia/Tokyo")
FLUSH_INTERVAL_SECONDS = 30.0


@dataclass(frozen=True)
class ActivityKey:
    user_id: int
    day: date
    route: str
    method: str
    agency_id: int | None
    via_api_key: bool


class ActivityBuffer:
    def __init__(self) -> None:
        self._counts: dict[ActivityKey, list[int]] = {}

    def record(self, key: ActivityKey, *, is_error: bool) -> None:
        counts = self._counts.setdefault(key, [0, 0])
        counts[0] += 1
        if is_error:
            counts[1] += 1

    def drain(self) -> dict[ActivityKey, list[int]]:
        batch, self._counts = self._counts, {}
        return batch


BUFFER = ActivityBuffer()


def today_jst() -> date:
    return datetime.now(_JST).date()


def agency_id_from(path_params: dict | None) -> int | None:
    raw = (path_params or {}).get("agency_id")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


# Rows whose user was deleted since they were counted are skipped rather than
# left to fail the foreign key, which would drop every other user's counts in
# the same batch.
_UPSERT_SQL = """
    INSERT INTO user_activity_daily (user_id, day, route, method, agency_id, via_api_key, requests, errors)
    SELECT u.user_id, u.day, u.route, u.method, u.agency_id, u.via_api_key, u.requests, u.errors
    FROM unnest($1::int[], $2::date[], $3::text[], $4::text[], $5::int[], $6::boolean[], $7::int[], $8::int[])
        AS u(user_id, day, route, method, agency_id, via_api_key, requests, errors)
    WHERE EXISTS (SELECT 1 FROM users WHERE users.user_id = u.user_id)
    ON CONFLICT (user_id, day, route, method, (COALESCE(agency_id, 0)), via_api_key) DO UPDATE SET
        requests = user_activity_daily.requests + EXCLUDED.requests,
        errors   = user_activity_daily.errors + EXCLUDED.errors
"""


async def flush(buffer: ActivityBuffer, pool: Any) -> int:
    """Write and forget ``buffer``'s counts; return how many keys were sent."""
    batch = buffer.drain()
    if not batch:
        return 0
    keys = list(batch)
    try:
        await pool.execute(
            _UPSERT_SQL,
            [k.user_id for k in keys],
            [k.day for k in keys],
            [k.route for k in keys],
            [k.method for k in keys],
            [k.agency_id for k in keys],
            [k.via_api_key for k in keys],
            [batch[k][0] for k in keys],
            [batch[k][1] for k in keys],
        )
    except Exception:
        _log.warning("Dropped %d usage count(s): the flush to user_activity_daily failed", len(keys), exc_info=True)
        return 0
    return len(keys)


async def _flush_periodically(app: Any) -> None:
    while True:
        await asyncio.sleep(FLUSH_INTERVAL_SECONDS)
        await flush(BUFFER, app.state.pool)


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
        await flush(BUFFER, pool)
