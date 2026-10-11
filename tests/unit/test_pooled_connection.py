"""PooledConnection borrows a pooled connection per statement and nothing more."""

import pytest

from pipeline.query.pooled_conn import PooledConnection, UnsupportedConnectionMethod


class _FakeConn:
    def __init__(self, log):
        self._log = log

    async def fetch(self, *a, **k):
        self._log.append(("fetch", a))
        return ["row"]

    async def fetchrow(self, *a, **k):
        self._log.append(("fetchrow", a))
        return {"x": 1}

    async def fetchval(self, *a, **k):
        self._log.append(("fetchval", a))
        return 7

    async def execute(self, *a, **k):
        self._log.append(("execute", a))
        return "OK"

    async def executemany(self, *a, **k):
        self._log.append(("executemany", a))


class _FakePool:
    def __init__(self):
        self.log: list = []
        self.in_use = 0
        self.acquired = 0

    def acquire(self):
        pool = self

        class _Ctx:
            async def __aenter__(self):
                pool.in_use += 1
                pool.acquired += 1
                return _FakeConn(pool.log)

            async def __aexit__(self, *exc):
                pool.in_use -= 1

        return _Ctx()


@pytest.mark.asyncio
async def test_each_statement_borrows_one_connection_and_gives_it_back():
    pool = _FakePool()
    conn = PooledConnection(pool)

    assert await conn.fetch("SELECT 1") == ["row"]
    assert await conn.fetchrow("SELECT 2") == {"x": 1}
    assert await conn.fetchval("SELECT 3") == 7
    assert await conn.execute("UPDATE t SET a=1") == "OK"
    await conn.executemany("INSERT INTO t VALUES ($1)", [(1,), (2,)])

    assert [name for name, _ in pool.log] == ["fetch", "fetchrow", "fetchval", "execute", "executemany"]
    assert pool.acquired == 5
    assert pool.in_use == 0


@pytest.mark.asyncio
async def test_a_connection_is_returned_even_when_the_statement_fails():
    pool = _FakePool()

    class _Boom(_FakeConn):
        async def fetch(self, *a, **k):
            raise RuntimeError("boom")

    pool.acquire = lambda: _BoomCtx(pool, _Boom)  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        await PooledConnection(pool).fetch("SELECT 1")
    assert pool.in_use == 0


class _BoomCtx:
    def __init__(self, pool, conn_cls):
        self._pool, self._cls = pool, conn_cls

    async def __aenter__(self):
        self._pool.in_use += 1
        return self._cls(self._pool.log)

    async def __aexit__(self, *exc):
        self._pool.in_use -= 1


@pytest.mark.parametrize("name", ["transaction", "cursor", "prepare", "copy_records_to_table", "add_listener"])
def test_anything_that_needs_one_connection_across_statements_is_refused(name):
    conn = PooledConnection(_FakePool())
    with pytest.raises(UnsupportedConnectionMethod, match=name):
        getattr(conn, name)


def test_a_missing_method_still_reads_as_missing_to_hasattr():
    assert not hasattr(PooledConnection(_FakePool()), "transaction")


@pytest.mark.asyncio
async def test_a_tool_that_needs_an_unsupported_method_is_a_failure_not_a_polite_answer(monkeypatch):
    """The Ask path turns tool errors into a 200 answer; a call the pooled
    connection cannot serve is a programming error and must not be hidden there."""
    from api.range import RangeCtx  # noqa: F401  (ctx type only)
    from pipeline.query import chat

    async def uses_a_transaction(*args, **kwargs):
        PooledConnection(_FakePool()).transaction  # noqa: B018

    monkeypatch.setattr(chat, "dispatch", uses_a_transaction)
    with pytest.raises(UnsupportedConnectionMethod):
        await chat._dispatch_and_respond("top_n", {}, None, PooledConnection(_FakePool()), 1, "ja", None)
