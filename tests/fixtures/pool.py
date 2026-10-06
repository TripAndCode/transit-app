"""A stand-in for `app.state.pool` that counts the connections it has out."""

from __future__ import annotations

from typing import Any


class CountingPool:
    """Hands out the one fake connection and counts how many are held."""

    def __init__(self, conn: Any):
        self._conn = conn
        self.held = 0
        self.peak = 0

    def acquire(self):
        pool = self

        class _Ctx:
            async def __aenter__(self):
                pool.held += 1
                pool.peak = max(pool.peak, pool.held)
                return pool._conn

            async def __aexit__(self, *exc):
                pool.held -= 1
                return False

        return _Ctx()
