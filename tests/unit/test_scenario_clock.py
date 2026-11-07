"""The scenario runner's clock, at the minutes of the day closest to its edges.

The runner reads the clock once and anchors every scenario on it. A run just
before 04:00 JST anchors furthest back, three calendar days, which is the
worst case for updates_live's TTL; a run just before midnight leaves the
least room before today.
"""

import re
from datetime import datetime, time, timedelta
from pathlib import Path

import pytest

from tests.fixtures.scenario import poll_batches
from tests.fixtures.scenario_clock import JST, LIVE_TTL, RUN_BOUND, anchor_day, check_closed_and_live
from tests.pipeline.test_scenarios import (
    cancellations,
    night_route,
    plain_weekday,
    timetable_change,
    timetable_published_early,
)

_SCENARIOS = (plain_weekday, night_route, timetable_change, timetable_published_early, cancellations)
_DAY = datetime(2026, 11, 8).date()


def _at(clock: str) -> datetime:
    hours, minutes = (int(part) for part in clock.split(":"))
    return datetime.combine(_DAY, time(hours, minutes), tzinfo=JST)


def _captures(build, now: datetime) -> list[datetime]:
    return [poll.captured_at for _version, batch in poll_batches(build(anchor_day(now))) for poll in batch]


@pytest.mark.parametrize("clock", ["00:00", "03:59", "04:00", "12:00", "23:59"])
@pytest.mark.parametrize("build", _SCENARIOS, ids=lambda build: build.__name__)
def test_every_scenario_fits_the_live_window_whenever_it_runs(build, clock):
    now = _at(clock)
    check_closed_and_live(_captures(build, now), now)


def test_a_run_just_before_the_service_day_turns_anchors_three_calendar_days_back():
    assert anchor_day(_at("03:59")) == _DAY - timedelta(days=3)
    assert anchor_day(_at("04:00")) == _DAY - timedelta(days=2)


def test_the_oldest_poll_of_the_worst_minute_leaves_hours_before_updates_live_drops_it():
    now = _at("03:59")
    oldest = min(min(_captures(build, now)) for build in _SCENARIOS)
    assert oldest - (now - LIVE_TTL + RUN_BOUND) > timedelta(hours=2)


def test_a_poll_the_ttl_would_drop_during_the_run_is_refused():
    now = _at("12:00")
    with pytest.raises(RuntimeError, match="leave updates_live"):
        check_closed_and_live([now - LIVE_TTL + RUN_BOUND], now)
    check_closed_and_live([now - LIVE_TTL + RUN_BOUND + timedelta(seconds=1)], now)


def test_a_poll_on_today_is_refused():
    now = _at("12:00")
    with pytest.raises(RuntimeError, match="not closed"):
        check_closed_and_live([_at("00:00")], now)


def test_the_live_ttl_is_the_one_the_schema_declares():
    schema = (Path(__file__).resolve().parents[2] / "db" / "clickhouse" / "schema.sql").read_text()
    table = schema[schema.index("CREATE TABLE IF NOT EXISTS updates_live") :]
    ttl = re.search(r"TTL toDateTime\(captured_at\) \+ INTERVAL (\d+) DAY", table[: table.index(";")])
    assert ttl is not None
    assert timedelta(days=int(ttl[1])) == LIVE_TTL
