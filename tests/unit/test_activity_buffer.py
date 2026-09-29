import asyncio
from datetime import date

from api.activity import ActivityBuffer, ActivityKey, agency_id_from, flush

_KEY = ActivityKey(1, date(2026, 1, 5), "/api/{agency_id}/overview/summary", "GET", 3, False)


def test_counts_add_up_per_key_and_errors_separately():
    buf = ActivityBuffer()
    buf.record(_KEY, is_error=False)
    buf.record(_KEY, is_error=True)
    assert buf.drain() == {_KEY: [2, 1]}


def test_drain_empties_the_buffer():
    buf = ActivityBuffer()
    buf.record(_KEY, is_error=False)
    buf.drain()
    assert buf.drain() == {}


def test_agency_id_comes_from_the_path_parameter_when_it_is_a_number():
    assert agency_id_from({"agency_id": "7"}) == 7
    assert agency_id_from({"agency_id": "nope"}) is None
    assert agency_id_from({}) is None
    assert agency_id_from(None) is None


class _FailingPool:
    async def execute(self, *args):
        raise ConnectionError("db down")


def test_a_failed_flush_drops_its_batch_instead_of_keeping_it():
    buf = ActivityBuffer()
    buf.record(_KEY, is_error=False)
    assert asyncio.run(flush(buf, _FailingPool())) == 0
    assert buf.drain() == {}
