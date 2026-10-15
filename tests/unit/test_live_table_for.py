"""The one place readers learn which table holds a service day. The current
service day and the one before it read `updates_live`; the switch to `updates`
happens at the 04:00 JST cutover (19:00 UTC the day before), never at JST or
UTC midnight."""

from datetime import date, datetime, timezone

import pytest

from pipeline.clickhouse import LIVE_TABLE, UPDATES_TABLE, checked_table, jst_midnight_utc, live_table_for

LAST_SECOND = datetime(2026, 10, 2, 14, 59, 59, tzinfo=timezone.utc)  # 2026-10-02 23:59:59 JST
MIDNIGHT = datetime(2026, 10, 2, 15, 0, 0, tzinfo=timezone.utc)  # 2026-10-03 00:00:00 JST
AFTER_MIDNIGHT = datetime(2026, 10, 2, 16, 30, 0, tzinfo=timezone.utc)  # 2026-10-03 01:30:00 JST
BEFORE_CUTOVER = datetime(2026, 10, 2, 18, 59, 59, tzinfo=timezone.utc)  # 2026-10-03 03:59:59 JST
CUTOVER = datetime(2026, 10, 2, 19, 0, 0, tzinfo=timezone.utc)  # 2026-10-03 04:00:00 JST
AFTER_CUTOVER = datetime(2026, 10, 2, 19, 30, 0, tzinfo=timezone.utc)  # 2026-10-03 04:30:00 JST
UTC_MIDNIGHT = datetime(2026, 10, 3, 0, 0, 0, tzinfo=timezone.utc)  # 2026-10-03 09:00:00 JST


def test_after_midnight_the_current_service_day_and_the_one_before_read_live():
    assert live_table_for(date(2026, 10, 2), now=AFTER_MIDNIGHT) == LIVE_TABLE
    assert live_table_for(date(2026, 10, 1), now=AFTER_MIDNIGHT) == LIVE_TABLE
    assert live_table_for(date(2026, 9, 30), now=AFTER_MIDNIGHT) == UPDATES_TABLE


def test_after_the_cutover_the_day_two_back_reads_updates():
    assert live_table_for(date(2026, 10, 3), now=AFTER_CUTOVER) == LIVE_TABLE
    assert live_table_for(date(2026, 10, 2), now=AFTER_CUTOVER) == LIVE_TABLE
    assert live_table_for(date(2026, 10, 1), now=AFTER_CUTOVER) == UPDATES_TABLE


def test_the_switch_happens_at_the_cutover():
    assert live_table_for(date(2026, 10, 1), now=BEFORE_CUTOVER) == LIVE_TABLE
    assert live_table_for(date(2026, 10, 1), now=CUTOVER) == UPDATES_TABLE


def test_jst_and_utc_midnight_are_not_boundaries():
    for day in (date(2026, 10, 1), date(2026, 10, 2)):
        assert live_table_for(day, now=LAST_SECOND) == live_table_for(day, now=MIDNIGHT) == LIVE_TABLE
    assert live_table_for(date(2026, 9, 30), now=MIDNIGHT) == UPDATES_TABLE
    assert live_table_for(date(2026, 10, 2), now=UTC_MIDNIGHT) == LIVE_TABLE
    assert live_table_for(date(2026, 10, 1), now=UTC_MIDNIGHT) == UPDATES_TABLE


def test_a_future_day_reads_live():
    assert live_table_for(date(2026, 10, 9), now=MIDNIGHT) == LIVE_TABLE


def test_jst_midnight_is_fifteen_hundred_utc_the_day_before():
    assert jst_midnight_utc(date(2026, 10, 3)) == MIDNIGHT


def test_only_the_two_observation_tables_reach_sql():
    assert checked_table("updates_live") == "updates_live"
    with pytest.raises(ValueError):
        checked_table("updates; DROP TABLE agencies")
