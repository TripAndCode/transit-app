"""Why an agency's RT-optional fields are not trusted.

`pipeline.strategies.static_join.coverage_gap` names the reason the gate is
closed, so a page can state the true cause: a feed that cannot carry the
fields at all, a check that found them missing, a check that has lapsed, or
one that never ran. The parity with the gate itself is pinned against a real
database in tests/pipeline/test_rt_coverage_registry.py.
"""

from datetime import date, datetime, timedelta, timezone

import pytest

from pipeline.strategies.static_join import RT_COVERAGE_FIELDS, CoverageGap, coverage_gap

NOW = datetime(2026, 10, 28, 3, 0, tzinfo=timezone.utc)
# 2026-09-10 16:30 UTC is already 2026-09-11 in JST.
PROBED = datetime(2026, 9, 10, 16, 30, tzinfo=timezone.utc)


def _rows(*, confirmed=True, expires_at=NOW + timedelta(days=30), probed_at=PROBED, fields=RT_COVERAGE_FIELDS):
    return [{"field_name": f, "confirmed": confirmed, "expires_at": expires_at, "probed_at": probed_at} for f in fields]


def test_live_complete_verdict_has_no_gap():
    assert coverage_gap("static_join", _rows(), NOW) is None


def test_non_expiring_verdict_has_no_gap():
    assert coverage_gap("static_join", _rows(expires_at=None), NOW) is None


def test_a_strategy_that_cannot_carry_the_fields_is_unsupported_whatever_the_registry_says():
    assert coverage_gap("aomori_regex", _rows(), NOW) == CoverageGap("feed_unsupported", None)
    assert coverage_gap(None, [], NOW) == CoverageGap("feed_unsupported", None)


def test_no_rows_is_never_probed():
    assert coverage_gap("static_join", [], NOW) == CoverageGap("never_probed", None)


def test_an_expired_verdict_awaits_a_recheck_and_names_the_jst_day_of_the_last_check():
    gap = coverage_gap("static_join", _rows(expires_at=NOW - timedelta(days=17)), NOW)
    assert gap == CoverageGap("probe_expired", date(2026, 9, 11))


def test_a_verdict_expiring_exactly_now_has_expired():
    assert coverage_gap("static_join", _rows(expires_at=NOW), NOW).reason == "probe_expired"


def test_a_partial_verdict_awaits_a_recheck():
    gap = coverage_gap("static_join", _rows(fields=RT_COVERAGE_FIELDS[:2]), NOW)
    assert gap == CoverageGap("probe_expired", date(2026, 9, 11))


def test_a_live_refutation_wins_over_an_expired_field():
    rows = _rows(expires_at=NOW - timedelta(days=1))
    rows[0] = {**rows[0], "confirmed": False, "expires_at": NOW + timedelta(days=1)}
    assert coverage_gap("static_join", rows, NOW).reason == "probe_refuted"


def test_an_expired_refutation_is_only_awaiting_a_recheck():
    rows = _rows(confirmed=False, expires_at=NOW - timedelta(days=1))
    assert coverage_gap("static_join", rows, NOW).reason == "probe_expired"


def test_last_checked_is_the_latest_probe():
    rows = _rows(expires_at=NOW - timedelta(days=1))
    rows[1] = {**rows[1], "probed_at": PROBED + timedelta(days=3)}
    assert coverage_gap("static_join", rows, NOW).last_checked == date(2026, 9, 14)


def test_rows_for_fields_outside_the_gate_are_ignored():
    rows = [{"field_name": "feed_timestamp", "confirmed": True, "expires_at": None, "probed_at": PROBED}]
    assert coverage_gap("static_join", rows, NOW) == CoverageGap("never_probed", None)


@pytest.mark.parametrize("reason", ["feed_unsupported", "probe_refuted", "probe_expired", "never_probed"])
def test_payload_spells_the_reason_and_an_iso_day(reason):
    assert CoverageGap(reason, date(2026, 9, 11)).payload() == {
        "unavailable_reason": reason,
        "last_checked": "2026-09-11",
    }
    assert CoverageGap(reason, None).payload()["last_checked"] is None
