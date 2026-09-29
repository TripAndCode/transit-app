"""Daily deletes that keep personal data within the privacy policy's periods.

The statements come from ``pipeline/retention.py``. Each runs on its own, so
one that fails is logged and the others still run; the next day's pass
catches up whatever a failure left behind. Several API processes each
running the pass is harmless: the deletes are idempotent.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from pipeline.retention import PERSONAL_DATA_RETENTION_MONTHS, personal_data_prune_sql

_log = logging.getLogger(__name__)

PRUNE_INTERVAL_SECONDS = 24 * 60 * 60


async def prune_once(pool: Any) -> None:
    for sql in personal_data_prune_sql(PERSONAL_DATA_RETENTION_MONTHS):
        table = sql.split()[2]
        try:
            result = await pool.execute(sql)
        except Exception:
            _log.warning("Personal-data prune of %s failed; the next daily pass retries it", table, exc_info=True)
            continue
        _log.info("Personal-data prune of %s: %s", table, result)


async def _prune_daily(app: Any) -> None:
    while True:
        await prune_once(app.state.pool)
        await asyncio.sleep(PRUNE_INTERVAL_SECONDS)


def start(app: Any) -> None:
    app.state.retention_pruner = asyncio.create_task(_prune_daily(app))


async def stop(app: Any) -> None:
    task = getattr(app.state, "retention_pruner", None)
    if task is not None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
