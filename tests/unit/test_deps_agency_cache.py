"""get_agency answers from a 30 s cache after one positive lookup, so the
hottest dependency in the API stops costing a pool acquire per request."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from api import deps


class _Ctx:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


class _Conn:
    def __init__(self, live: set[int]):
        self.live = live

    async def fetchrow(self, sql, agency_id):
        assert "deleted_at IS NULL" in sql
        return {"agency_id": agency_id} if agency_id in self.live else None


class _Pool:
    def __init__(self, live: set[int]):
        self.conn = _Conn(live)
        self.acquires = 0

    def acquire(self):
        self.acquires += 1
        return _Ctx(self.conn)


def _request(pool):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(pool=pool)))


@pytest.fixture(autouse=True)
def _fresh_cache():
    deps.reset_agency_cache()
    yield
    deps.reset_agency_cache()


@pytest.mark.asyncio
async def test_a_second_lookup_within_the_ttl_touches_no_connection(monkeypatch):
    pool = _Pool({7})
    now = [1000.0]
    monkeypatch.setattr(deps.time, "monotonic", lambda: now[0])
    assert await deps.get_agency(7, _request(pool)) == 7
    now[0] += deps.AGENCY_CACHE_TTL_SECONDS - 1
    assert await deps.get_agency(7, _request(pool)) == 7
    assert pool.acquires == 1


@pytest.mark.asyncio
async def test_the_entry_expires_after_the_ttl(monkeypatch):
    pool = _Pool({7})
    now = [1000.0]
    monkeypatch.setattr(deps.time, "monotonic", lambda: now[0])
    await deps.get_agency(7, _request(pool))
    now[0] += deps.AGENCY_CACHE_TTL_SECONDS
    await deps.get_agency(7, _request(pool))
    assert pool.acquires == 2


@pytest.mark.asyncio
async def test_get_agency_does_not_cache_a_miss():
    """Review-focus item 5: a 404 must not stick for 30 s once the agency exists."""
    pool = _Pool(set())
    with pytest.raises(HTTPException) as exc:
        await deps.get_agency(9, _request(pool))
    assert exc.value.status_code == 404
    pool.conn.live.add(9)
    assert await deps.get_agency(9, _request(pool)) == 9
    assert pool.acquires == 2


@pytest.mark.asyncio
async def test_invalidate_agency_forces_the_next_lookup_to_the_database():
    pool = _Pool({7})
    await deps.get_agency(7, _request(pool))
    deps.invalidate_agency(7)
    pool.conn.live.discard(7)
    with pytest.raises(HTTPException):
        await deps.get_agency(7, _request(pool))
    assert pool.acquires == 2


def test_the_cache_is_swept_with_every_other_compute_cache():
    from pipeline.cache import _REGISTERED_CLEARS

    assert deps.reset_agency_cache in _REGISTERED_CLEARS


def test_disable_and_restore_invalidate_the_agency_cache():
    """Review-focus item 5, write side: the two writes that flip deleted_at
    call invalidate_agency, so a disabled agency stops validating at once."""
    import inspect

    from api.routers import agencies

    source = inspect.getsource(agencies)
    for marker in ("UPDATE agencies SET deleted_at = now()", "UPDATE agencies SET deleted_at = NULL"):
        at = source.index(marker)
        assert "invalidate_agency(" in source[at : at + 1200], marker
