"""An agency's capability tier, derived from what its feed can support.

`pipeline.reports.capability.capability_tier` reads the same facts as the RT
coverage gate (`pipeline.strategies.static_join.coverage_gap`) and the
comparability gate's trip matching (`pipeline.reports.comparability`), so a
feed that cannot carry the timetable-joined figures is labelled by that,
while a capable feed awaiting a check keeps its pipeline-state reason.
"""

from datetime import datetime, timedelta, timezone

import pytest

from pipeline.reports.capability import capability_tier
from pipeline.strategies.static_join import RT_COVERAGE_FIELDS

NOW = datetime(2026, 10, 28, 3, 0, tzinfo=timezone.utc)
PROBED = datetime(2026, 9, 10, 16, 30, tzinfo=timezone.utc)


def _rows(*, confirmed=True, expires_at=NOW + timedelta(days=30), fields=RT_COVERAGE_FIELDS):
    return [{"field_name": f, "confirmed": confirmed, "expires_at": expires_at, "probed_at": PROBED} for f in fields]


def test_a_matched_feed_with_a_live_complete_verdict_is_full():
    assert capability_tier("static_join", _rows(), NOW) == "full"


def test_a_strategy_whose_trips_cannot_be_matched_is_basic_whatever_the_registry_says():
    assert capability_tier("aomori_regex", _rows(), NOW) == "basic"
    assert capability_tier(None, [], NOW) == "basic"


def test_a_live_refutation_is_basic():
    assert capability_tier("static_join", _rows(confirmed=False), NOW) == "basic"


@pytest.mark.parametrize(
    "rows",
    [
        pytest.param([], id="never_probed"),
        pytest.param(_rows(expires_at=NOW - timedelta(days=1)), id="expired"),
        pytest.param(_rows(fields=RT_COVERAGE_FIELDS[:2]), id="partial"),
        pytest.param(_rows(confirmed=False, expires_at=NOW - timedelta(days=1)), id="expired_refutation"),
    ],
)
def test_a_capable_feed_awaiting_a_check_is_full(rows):
    assert capability_tier("static_join", rows, NOW) == "full"
