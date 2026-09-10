"""Pure-logic tests for the RT field-coverage threshold check.

No DB, no network. `pipeline.strategies.static_join.assess_field_coverage`
is the canonical verdict -- the same call `--record` persists to
`rt_field_coverage_probes` -- and `_assess` is the probe CLI's human-facing
report built on top of it; both are covered here so the report can't drift
from what gets recorded. `_record`'s source-vs-agency check is covered too,
against a stub connection -- it decides whether anything is written at all,
before any DB round trip that matters. Fetching a live feed needs real
network and isn't covered here.
"""

from __future__ import annotations

import asyncpg
import pytest

from pipeline.strategies.static_join import RT_COVERAGE_FIELDS, assess_field_coverage
from scripts import probe_rt_field_coverage as probe
from scripts.probe_rt_field_coverage import _assess

_FEED_URL = "https://feeds.example.jp/realtime/8/trip_updates.bin"


def _confirmed_cov() -> dict:
    """A field_coverage() result shaped like a verified feed's."""
    return {
        "stop_time_updates": 100,
        "feed_timestamp": 1_770_000_000,
        "stop_id_coverage": 1.0,
        "arr_delay_coverage": 0.2,
        "schedule_relationship_trip_coverage": 1.0,
        "schedule_relationship_stop_coverage": 1.0,
    }


class _StubConn:
    """The one query `_record` makes before deciding to write."""

    def __init__(self, feed_url: str | None):
        self._feed_url = feed_url
        self.closed = False

    async def fetchval(self, _sql, *_args):
        return self._feed_url

    async def close(self):
        self.closed = True


@pytest.fixture
def stub_db(monkeypatch):
    """Point `_record` at a stub agencies row, and capture what it records.

    Returns a callable taking the agency's stored feed_url and yielding the
    list writes land in, so a test asserting nothing was written asserts on
    the same list a successful write would append to.
    """
    monkeypatch.setenv("DATABASE_URL", "postgresql://stub/stub")
    recorded: list[tuple] = []

    async def _fake_record(conn, agency_id, cov, source_feed, ttl_days):
        recorded.append((agency_id, source_feed, ttl_days))

    monkeypatch.setattr(probe, "record_field_coverage_probe", _fake_record)

    def _arrange(feed_url):
        async def _fake_connect(_url):
            return _StubConn(feed_url)

        monkeypatch.setattr(asyncpg, "connect", _fake_connect)
        return recorded

    return _arrange


@pytest.mark.asyncio
async def test_record_refuses_a_url_that_is_not_the_agencys_own_feed(stub_db):
    """These vendor feeds differ only by an operator number in the path, so
    probing one operator's feed against another's --agency-id is an easy
    slip -- and would record a full set of affirmative verdicts for a feed
    that agency does not even read."""
    recorded = stub_db(_FEED_URL)
    other_feed = _FEED_URL.replace("/8/", "/12/")

    with pytest.raises(ValueError, match="feed_url"):
        await probe._record(12, _confirmed_cov(), other_feed, 180, probed_url=other_feed)

    assert recorded == []


@pytest.mark.asyncio
async def test_record_accepts_the_agencys_own_feed_url(stub_db):
    recorded = stub_db(_FEED_URL)

    await probe._record(8, _confirmed_cov(), _FEED_URL, 180, probed_url=_FEED_URL)

    assert recorded == [(8, _FEED_URL, 180)]


@pytest.mark.asyncio
async def test_record_accepts_a_local_capture_for_any_agency(stub_db):
    """--file is the deliberate escape hatch: a capture path can never equal
    a feed URL, so there is nothing to match and the operator vouches for
    where the sample came from. The recorded source_feed names the capture."""
    recorded = stub_db(_FEED_URL)

    await probe._record(8, _confirmed_cov(), "tests/fixtures/hiroden_tu.bin", None, probed_url=None)

    assert recorded == [(8, "tests/fixtures/hiroden_tu.bin", None)]


@pytest.mark.asyncio
async def test_record_reports_an_unknown_agency_id_plainly(stub_db):
    recorded = stub_db(None)

    with pytest.raises(ValueError, match="no agencies row"):
        await probe._record(999, _confirmed_cov(), _FEED_URL, 180, probed_url=_FEED_URL)

    assert recorded == []


def test_assess_field_coverage_returns_none_for_an_empty_capture():
    """An empty poll is not evidence either way -- distinct from a verdict
    of "this feed never sends the field"."""
    assert assess_field_coverage({"stop_time_updates": 0, "feed_timestamp": None}) is None


def test_assess_field_coverage_verdicts_are_keyed_by_registry_field_name():
    """Keys must match RT_COVERAGE_FIELDS exactly: they are written straight
    into rt_field_coverage_probes.field_name, whose CHECK constraint (and the
    read-side gate's completeness count) depends on that spelling."""
    cov = {
        "stop_time_updates": 100,
        "feed_timestamp": 1_770_000_000,
        "stop_id_coverage": 1.0,
        "arr_delay_coverage": 0.2,
        "schedule_relationship_trip_coverage": 1.0,
        "schedule_relationship_stop_coverage": 1.0,
    }
    verdicts = assess_field_coverage(cov)
    assert set(verdicts) == set(RT_COVERAGE_FIELDS)
    assert all(verdicts.values())


def test_assess_empty_feed_has_no_stop_time_updates():
    result = _assess({"stop_time_updates": 0, "feed_timestamp": None})
    assert "no stop_time_updates" in result["assessment"]
    assert "matches_confirmed_agencies" not in result


def test_assess_matches_confirmed_agency_coverage():
    """Coverage shaped like the real hiroden/hirobus/hirokoh fixtures
    (tests/pipeline/test_static_join.py's own thresholds) must match on
    every field."""
    cov = {
        "stop_time_updates": 100,
        "feed_timestamp": 1_770_000_000,
        "stop_id_coverage": 1.0,
        "arr_delay_coverage": 0.2,
        "schedule_relationship_trip_coverage": 1.0,
        "schedule_relationship_stop_coverage": 1.0,
    }
    result = _assess(cov)
    assert all(result["matches_confirmed_agencies"].values())


def test_assess_flags_a_feed_that_never_sends_schedule_relationship():
    """A new agency whose feed never populates schedule_relationship_* must
    NOT be silently reported as matching -- a static_join agency's optional
    fields are not guaranteed populated just because the platform matches an
    already-confirmed agency, and downstream service_delivered/dwell_run
    logic must not assume availability without this check."""
    cov = {
        "stop_time_updates": 100,
        "feed_timestamp": 1_770_000_000,
        "stop_id_coverage": 1.0,
        "arr_delay_coverage": 0.2,
        "schedule_relationship_trip_coverage": 0.0,
        "schedule_relationship_stop_coverage": 0.0,
    }
    result = _assess(cov)
    checks = result["matches_confirmed_agencies"]
    assert checks["schedule_relationship_trip_coverage"] is False
    assert checks["schedule_relationship_stop_coverage"] is False
    assert checks["stop_id_coverage"] is True


def test_assess_flags_arr_delay_coverage_outside_sparse_range():
    """arr_delay is only sent when a StopTimeUpdate carries an `arrival`
    submessage, so genuine sparsity is expected -- either always-present
    (1.0, suggesting a different field semantics) or always-absent (0.0) is
    flagged, not silently accepted."""
    cov = {
        "stop_time_updates": 100,
        "feed_timestamp": 1_770_000_000,
        "stop_id_coverage": 1.0,
        "arr_delay_coverage": 1.0,
        "schedule_relationship_trip_coverage": 1.0,
        "schedule_relationship_stop_coverage": 1.0,
    }
    result = _assess(cov)
    assert result["matches_confirmed_agencies"]["arr_delay_coverage"] is False


def test_assess_flags_implausible_feed_timestamp():
    cov = {
        "stop_time_updates": 100,
        "feed_timestamp": 1,  # plausible enum value, not a real epoch second
        "stop_id_coverage": 1.0,
        "arr_delay_coverage": 0.2,
        "schedule_relationship_trip_coverage": 1.0,
        "schedule_relationship_stop_coverage": 1.0,
    }
    result = _assess(cov)
    assert result["matches_confirmed_agencies"]["feed_timestamp"] is False
