"""The one place readers learn which table holds a JST day. A day closes at
JST midnight, 15:00 UTC the day before, never at UTC midnight."""

from datetime import date, datetime, timezone

import pytest

from pipeline.clickhouse import LIVE_TABLE, UPDATES_TABLE, checked_table, jst_midnight_utc, live_table_for

LAST_SECOND = datetime(2026, 10, 2, 14, 59, 59, tzinfo=timezone.utc)  # 2026-10-02 23:59:59 JST
MIDNIGHT = datetime(2026, 10, 2, 15, 0, 0, tzinfo=timezone.utc)  # 2026-10-03 00:00:00 JST
UTC_MIDNIGHT = datetime(2026, 10, 3, 0, 0, 0, tzinfo=timezone.utc)  # 2026-10-03 09:00:00 JST


def test_today_reads_live_through_the_last_second_of_the_jst_day():
    assert live_table_for(date(2026, 10, 2), now=LAST_SECOND) == LIVE_TABLE
    assert live_table_for(date(2026, 10, 1), now=LAST_SECOND) == UPDATES_TABLE


def test_the_day_closes_at_jst_midnight():
    assert live_table_for(date(2026, 10, 2), now=MIDNIGHT) == UPDATES_TABLE
    assert live_table_for(date(2026, 10, 3), now=MIDNIGHT) == LIVE_TABLE


def test_utc_midnight_is_not_a_boundary():
    assert live_table_for(date(2026, 10, 3), now=UTC_MIDNIGHT) == LIVE_TABLE
    assert live_table_for(date(2026, 10, 2), now=UTC_MIDNIGHT) == UPDATES_TABLE


def test_a_future_day_reads_live():
    assert live_table_for(date(2026, 10, 9), now=MIDNIGHT) == LIVE_TABLE


def test_jst_midnight_is_fifteen_hundred_utc_the_day_before():
    assert jst_midnight_utc(date(2026, 10, 3)) == MIDNIGHT


def test_only_the_two_observation_tables_reach_sql():
    assert checked_table("updates_live") == "updates_live"
    with pytest.raises(ValueError):
        checked_table("updates; DROP TABLE agencies")
