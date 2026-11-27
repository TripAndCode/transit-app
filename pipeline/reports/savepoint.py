"""A savepoint around a read that may fail, so the caller's transaction outlives it."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager


@asynccontextmanager
async def savepoint(conn) -> AsyncIterator[None]:
    """A savepoint inside *conn*'s open transaction, so a statement that fails
    within it leaves the transaction usable for the caller's next statement.

    None outside a transaction, where a failed statement leaves nothing
    behind, nor on a connection that runs each statement on its own and has
    no transactions at all (:class:`pipeline.query.pooled_conn.PooledConnection`).
    """
    in_transaction = getattr(conn, "is_in_transaction", None)
    if in_transaction is not None and in_transaction():
        async with conn.transaction():
            yield
    else:
        yield
