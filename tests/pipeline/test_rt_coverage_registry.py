"""The durable RT field-coverage registry behind every RT-optional-field gate.

Covers `rt_field_coverage_probes` end to end: what
`pipeline.strategies.static_join.record_field_coverage_probe` writes, and
what `rt_field_coverage_confirmed`/`rt_field_coverage_confirmed_agencies`
then trust, plus what `invalidate_field_coverage_probes` withdraws. The
point of the registry is that verifying a feed is a probe run rather than a
code change, so these tests drive it exclusively through those public entry
points.
"""

from datetime import datetime, timedelta, timezone

import pytest

from pipeline.strategies.static_join import (
    RT_COVERAGE_FIELDS,
    invalidate_field_coverage_probes,
    record_field_coverage_probe,
    rt_field_coverage_confirmed,
    rt_field_coverage_confirmed_agencies,
)

pytestmark = pytest.mark.asyncio


def _cov(stop_time_updates=100, **overrides):
    """A field_coverage() result shaped like a verified feed's."""
    cov = {
        "stop_time_updates": stop_time_updates,
        "feed_timestamp": 1_770_000_000,
        "stop_id_coverage": 1.0,
        "arr_delay_coverage": 0.2,
        "schedule_relationship_trip_coverage": 1.0,
        "schedule_relationship_stop_coverage": 1.0,
    }
    cov.update(overrides)
    return cov


async def _set_strategy(conn, aid, strategy):
    await conn.execute("UPDATE agencies SET ingest_strategy = $1 WHERE agency_id = $2", strategy, aid)


async def test_unprobed_agency_is_not_confirmed(aconn, aagency_id):
    """The production default: a static_join agency nobody has probed is
    untrusted, so a newly onboarded feed can never inherit another
    agency's verified coverage just by sharing its wire shape."""
    await _set_strategy(aconn, aagency_id, "static_join")
    assert await rt_field_coverage_confirmed(aagency_id, aconn) is False


async def test_recorded_probe_makes_an_agency_confirmed(aconn, aagency_id):
    """Recording a probe -- not editing code -- is what turns the gate on."""
    await _set_strategy(aconn, aagency_id, "static_join")
    verdicts = await record_field_coverage_probe(aconn, aagency_id, _cov(), "https://feed.example/tu.bin")

    assert verdicts == dict.fromkeys(RT_COVERAGE_FIELDS, True)
    assert await rt_field_coverage_confirmed(aagency_id, aconn) is True


async def test_recorded_probe_persists_provenance(aconn, aagency_id):
    """Each field row carries the measured fraction, the sample size, the
    feed it came from, and when -- enough to judge a stale verdict later
    without re-deriving it from logs."""
    await _set_strategy(aconn, aagency_id, "static_join")
    before = datetime.now(timezone.utc)
    await record_field_coverage_probe(aconn, aagency_id, _cov(), "https://feed.example/tu.bin", ttl_days=30)

    rows = await aconn.fetch(
        "SELECT field_name, confirmed, coverage, sample_size, source_feed, probed_at, expires_at "
        "FROM rt_field_coverage_probes WHERE agency_id = $1",
        aagency_id,
    )
    assert {r["field_name"] for r in rows} == set(RT_COVERAGE_FIELDS)
    by_field = {r["field_name"]: r for r in rows}
    assert by_field["stop_id"]["coverage"] == 1.0
    assert by_field["arr_delay"]["coverage"] == 0.2
    for r in rows:
        assert r["sample_size"] == 100
        assert r["source_feed"] == "https://feed.example/tu.bin"
        assert r["probed_at"] >= before - timedelta(seconds=5)
        # ~30 days out, checked loosely: the exact instant depends on the
        # DB clock, only the horizon is being asserted.
        assert timedelta(days=29) < r["expires_at"] - r["probed_at"] < timedelta(days=31)


async def test_expired_verdict_is_not_trusted(aconn, aagency_id):
    """A verdict that has aged out reads exactly like an unprobed agency:
    report availability follows currently-verified feed state, so a feed
    that may have changed shape since its last probe stops being trusted
    on its own rather than needing someone to notice."""
    await _set_strategy(aconn, aagency_id, "static_join")
    await record_field_coverage_probe(aconn, aagency_id, _cov(), "https://feed.example/tu.bin", ttl_days=30)
    await aconn.execute(
        "UPDATE rt_field_coverage_probes SET expires_at = now() - INTERVAL '1 day' WHERE agency_id = $1",
        aagency_id,
    )
    assert await rt_field_coverage_confirmed(aagency_id, aconn) is False


async def test_non_expiring_verdict_stays_trusted(aconn, aagency_id):
    """ttl_days=None records a verdict backed by something more durable
    than one live poll (e.g. a checked-in capture)."""
    await _set_strategy(aconn, aagency_id, "static_join")
    await record_field_coverage_probe(aconn, aagency_id, _cov(), "tests/fixtures/hiroden_tu.bin", ttl_days=None)

    row = await aconn.fetchrow(
        "SELECT expires_at FROM rt_field_coverage_probes WHERE agency_id = $1 LIMIT 1", aagency_id
    )
    assert row["expires_at"] is None
    assert await rt_field_coverage_confirmed(aagency_id, aconn) is True


async def test_refuted_field_blocks_the_whole_agency(aconn, aagency_id):
    """A feed that never sends schedule_relationship_* is recorded as such,
    and the gate closes rather than half-trusting the agency."""
    await _set_strategy(aconn, aagency_id, "static_join")
    verdicts = await record_field_coverage_probe(
        aconn,
        aagency_id,
        _cov(schedule_relationship_trip_coverage=0.0, schedule_relationship_stop_coverage=0.0),
        "https://feed.example/tu.bin",
    )

    assert verdicts["stop_id"] is True
    assert verdicts["schedule_relationship_trip"] is False
    assert await rt_field_coverage_confirmed(aagency_id, aconn) is False


async def test_re_probing_can_withdraw_trust(aconn, aagency_id):
    """A feed that regresses loses availability on the next probe, with no
    code change and no leftover row from the earlier affirmative run."""
    await _set_strategy(aconn, aagency_id, "static_join")
    await record_field_coverage_probe(aconn, aagency_id, _cov(), "https://feed.example/tu.bin")
    assert await rt_field_coverage_confirmed(aagency_id, aconn) is True

    await record_field_coverage_probe(aconn, aagency_id, _cov(stop_id_coverage=0.0), "https://feed.example/tu.bin")
    assert await rt_field_coverage_confirmed(aagency_id, aconn) is False
    count = await aconn.fetchval("SELECT count(*) FROM rt_field_coverage_probes WHERE agency_id = $1", aagency_id)
    assert count == len(RT_COVERAGE_FIELDS)


async def test_partial_verdict_is_not_enough(aconn, aagency_id):
    """A registry holding only some of the fields means the feed was never
    fully vetted -- not that the unrecorded fields are fine."""
    await _set_strategy(aconn, aagency_id, "static_join")
    await record_field_coverage_probe(aconn, aagency_id, _cov(), "https://feed.example/tu.bin")
    await aconn.execute(
        "DELETE FROM rt_field_coverage_probes WHERE agency_id = $1 AND field_name = 'arr_delay'",
        aagency_id,
    )
    assert await rt_field_coverage_confirmed(aagency_id, aconn) is False


async def test_ingest_strategy_half_of_the_gate_still_applies(aconn, aagency_id):
    """A recorded verdict does not override the ingest strategy: an
    aomori_regex feed cannot populate these fields at all, whatever a probe
    row says."""
    await _set_strategy(aconn, aagency_id, "aomori_regex")
    await record_field_coverage_probe(aconn, aagency_id, _cov(), "https://feed.example/tu.bin")
    assert await rt_field_coverage_confirmed(aagency_id, aconn) is False


async def test_empty_capture_is_refused_rather_than_recorded(aconn, aagency_id):
    """An overnight/empty poll proves nothing either way; writing it as a
    refutation would durably mark a fine feed unavailable."""
    await _set_strategy(aconn, aagency_id, "static_join")
    with pytest.raises(ValueError, match="no stop_time_updates"):
        await record_field_coverage_probe(
            aconn, aagency_id, {"stop_time_updates": 0, "feed_timestamp": None}, "https://feed.example/tu.bin"
        )

    count = await aconn.fetchval("SELECT count(*) FROM rt_field_coverage_probes WHERE agency_id = $1", aagency_id)
    assert count == 0


async def test_invalidation_closes_the_gate_again(aconn, aagency_id):
    """A verdict describes one feed, so whatever repoints an agency at a
    different feed discards it: the agency drops back to the unprobed
    default until a probe re-earns trust, rather than lending the new feed
    the old one's coverage."""
    await _set_strategy(aconn, aagency_id, "static_join")
    await record_field_coverage_probe(aconn, aagency_id, _cov(), "https://feed.example/tu.bin", ttl_days=None)
    assert await rt_field_coverage_confirmed(aagency_id, aconn) is True

    dropped = await invalidate_field_coverage_probes(aconn, aagency_id)

    assert dropped == len(RT_COVERAGE_FIELDS)
    assert await rt_field_coverage_confirmed(aagency_id, aconn) is False
    assert await invalidate_field_coverage_probes(aconn, aagency_id) == 0


async def test_batch_and_single_gate_agree(aconn, aagency_id):
    """The batch helper the network/service-delivered read path uses must
    return exactly the agencies the per-agency gate would admit."""
    other = await aconn.fetchval(
        "INSERT INTO agencies (agency_name, feed_url) VALUES ('Other', 'http://other') RETURNING agency_id"
    )
    await _set_strategy(aconn, aagency_id, "static_join")
    await _set_strategy(aconn, other, "static_join")
    await record_field_coverage_probe(aconn, aagency_id, _cov(), "https://feed.example/tu.bin")

    assert await rt_field_coverage_confirmed_agencies(aconn, [aagency_id, other]) == {aagency_id}
    assert await rt_field_coverage_confirmed_agencies(aconn, []) == set()
