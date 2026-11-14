"""`pipeline.reports.capability.capability_tiers` reads the registry the RT coverage
gate reads, and judges it as `capability_tier` does, by the database's clock."""

from datetime import datetime, timedelta, timezone

import pytest

from pipeline.reports.capability import capability_tiers
from tests.conftest import confirm_rt_field_coverage

pytestmark = pytest.mark.asyncio


async def _agency(conn, name, strategy):
    return await conn.fetchval(
        "INSERT INTO agencies (agency_name, feed_url, ingest_strategy) VALUES ($1, $2, $3) RETURNING agency_id",
        name,
        f"http://{name}.example.com/rt",
        strategy,
    )


async def test_tiers_follow_the_registry_one_agency_at_a_time(aconn):
    confirmed = await _agency(aconn, "confirmed", "static_join")
    lapsed = await _agency(aconn, "lapsed", "static_join")
    lapsed_refutation = await _agency(aconn, "lapsed-refutation", "static_join")
    unprobed = await _agency(aconn, "unprobed", "static_join")
    regex = await _agency(aconn, "regex", "aomori_regex")
    refuted = await _agency(aconn, "refuted", "static_join")
    past = datetime.now(timezone.utc) - timedelta(days=1)
    await confirm_rt_field_coverage(aconn, confirmed, regex)
    await confirm_rt_field_coverage(aconn, lapsed, expires_at=past)
    await confirm_rt_field_coverage(aconn, lapsed_refutation, confirmed=False, expires_at=past)
    await confirm_rt_field_coverage(aconn, refuted, confirmed=False)

    ids = [confirmed, lapsed, lapsed_refutation, unprobed, regex, refuted, 999_999]
    assert await capability_tiers(aconn, ids) == {
        confirmed: "full",
        lapsed: "full",
        lapsed_refutation: "full",
        unprobed: "full",
        regex: "basic",
        refuted: "basic",
    }


async def test_no_ids_asks_nothing(aconn):
    assert await capability_tiers(aconn, []) == {}
