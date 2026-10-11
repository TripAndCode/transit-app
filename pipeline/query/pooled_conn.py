"""A connection stand-in that borrows from the pool one statement at a time."""

from __future__ import annotations

from typing import Any


class UnsupportedConnectionMethod(AttributeError):
    """A method that needs one connection across several statements was called
    on a :class:`PooledConnection`. It subclasses ``AttributeError`` so
    ``hasattr`` still reads it as absent, and callers that turn tool failures
    into answers re-raise it, since it is a programming error, not a tool one."""


class PooledConnection:
    """Stands in for an ``asyncpg.Connection`` while holding none.

    Each call borrows a pooled connection for just that statement and returns
    it, so a request that spends most of its time waiting on something slow (an
    LLM call, for one) pins nothing while it waits. Only the single-statement
    methods exist. Anything that needs one connection across several
    statements, such as ``transaction()``, ``cursor()`` or ``prepare()``, raises
    :class:`UnsupportedConnectionMethod` rather than silently spanning
    connections.

    Each statement pays one acquire and the pool's reset on release, which is
    small next to the waits this exists for.
    """

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def fetch(self, *args: Any, **kwargs: Any) -> Any:
        async with self._pool.acquire() as conn:
            return await conn.fetch(*args, **kwargs)

    async def fetchrow(self, *args: Any, **kwargs: Any) -> Any:
        async with self._pool.acquire() as conn:
            return await conn.fetchrow(*args, **kwargs)

    async def fetchval(self, *args: Any, **kwargs: Any) -> Any:
        async with self._pool.acquire() as conn:
            return await conn.fetchval(*args, **kwargs)

    async def execute(self, *args: Any, **kwargs: Any) -> Any:
        async with self._pool.acquire() as conn:
            return await conn.execute(*args, **kwargs)

    async def executemany(self, *args: Any, **kwargs: Any) -> Any:
        async with self._pool.acquire() as conn:
            return await conn.executemany(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        raise UnsupportedConnectionMethod(
            f"PooledConnection has no {name!r}: it borrows one connection per statement, "
            "so it cannot provide anything that spans statements"
        )
