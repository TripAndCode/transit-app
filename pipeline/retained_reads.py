"""A live read of `updates` holds only while the retention floor stays at or
below where it starts.

pipeline.live_filing.require_retained checks the floor before a read scans.
A prune commits the raised floor first and drops the partitions after it
(pipeline.retention.prune_updates). So a read whose check passed just before
that commit can still be scanning, or start more scans, once its rows are
gone, and its answer would be partial.

Every check that passes is noted against the computation in progress. Once
the computation's last scan has ended, the floor of each agency it read is
read again (Reads.verify). The drop follows the commit, so a floor still at or
below every start the computation read from proves no drop reached those
reads. A floor past one of them refuses the whole computation with
RangeNotRetained, so its partial answer is neither returned, cached nor
stored.

A computation is whatever runs inside one `recording()`: each cached compute
(pipeline.cache.async_lru_cache), each Ask tool (pipeline.query.tools), and
each API request (api.range_not_retained.recheck_live_reads). A check is
noted against the innermost one, which verifies it; an enclosing one gets
only the verified result. Tasks a computation starts (asyncio.gather) copy
its context, and with it the same record, so their checks reach it. Outside
any computation nothing is noted or re-checked: analyze, for one, reads under
the agency locks a prune holds until it has dropped.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date
from typing import Any, TypeVar

T = TypeVar("T")

#: The machine code of a refused read (RangeNotRetained) on the API's 422.
RANGE_NOT_RETAINED_CODE = "range_not_retained"


class RangeNotRetained(Exception):
    """A live read of `updates` starts before *floor*, the agency's retention
    floor (pipeline.retention.retention_floor): `updates` no longer holds
    every row there, so the read would show a dropped month as one with no
    data, or give a partial answer across the floor."""

    def __init__(self, floor: date) -> None:
        super().__init__(f"raw readings before {floor.isoformat()} are no longer retained")
        self.floor = floor


class Reads:
    """The live reads of `updates` one computation has made: for each agency,
    the earliest service date read and the connection it was last checked
    on."""

    __slots__ = ("starts",)

    def __init__(self) -> None:
        self.starts: dict[int, tuple[date, Any]] = {}

    def note(self, conn: Any, agency_id: int, first: date) -> None:
        held = self.starts.get(agency_id)
        self.starts[agency_id] = (first if held is None else min(held[0], first), conn)

    async def verify(self, conn: Any = None) -> None:
        """Raise RangeNotRetained unless each agency's floor, read again now,
        is still at or below the earliest date read from it. Each floor is
        read on *conn*, or without one on the connection the agency was last
        checked on, which the caller must still hold."""
        if not self.starts:
            return
        # pipeline.retention imports pipeline.cache, which imports this module.
        from pipeline import retention

        pending = list(self.starts.items())
        for agency_id, (first, checked_on) in pending:
            floor = await retention.retention_floor_async(checked_on if conn is None else conn, agency_id)
            if floor is not None and first < floor:
                raise RangeNotRetained(floor)
        # A read noted while the floors were being read is left to the next verify.
        for agency_id, held in pending:
            if self.starts.get(agency_id) is held:
                del self.starts[agency_id]


_current: ContextVar[Reads | None] = ContextVar("retained_reads", default=None)


def note(conn: Any, agency_id: int, first: date) -> None:
    """Note that a read of *agency_id*'s `updates` from *first* passed its
    floor check on *conn*, against the computation in progress, if any."""
    reads = _current.get()
    if reads is not None:
        reads.note(conn, agency_id, first)


@contextmanager
def recording() -> Iterator[Reads]:
    """Run the body as one computation, noting its live reads in the record
    it yields. The caller verifies the record once the body has returned."""
    reads = Reads()
    token = _current.set(reads)
    try:
        yield reads
    finally:
        _current.reset(token)


async def checked(compute: Callable[[], Awaitable[T]], conn: Any = None) -> T:
    """``await compute()`` as one computation whose live reads are re-checked
    on *conn* (Reads.verify) before its result is returned.

    A re-check that fails computes once more rather than refusing. A span a
    reader derived itself, such as a baseline or a look-back, starts no earlier
    than the floor it read, so the second run starts it at the new floor,
    while a period the request named that now starts before it is refused by
    that run's own check. Unless the floor rises again during that run, a
    refusal names only a period the request asked for."""
    with recording() as reads:
        value = await compute()
    try:
        await reads.verify(conn)
    except RangeNotRetained:
        with recording() as reads:
            value = await compute()
        await reads.verify(conn)
    return value


async def checkpoint(conn: Any = None) -> None:
    """Verify the live reads the computation in progress has made so far, before
    the caller stores a result built from them. Those reads are then settled,
    and the computation verifies only what it reads after."""
    reads = _current.get()
    if reads is not None:
        await reads.verify(conn)
