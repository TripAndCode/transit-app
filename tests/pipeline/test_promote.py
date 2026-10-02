"""Daily promotion copies each closed JST day from updates_live into updates.

Data is stamped relative to the real clock (today and yesterday in JST):
updates_live's 3-day TTL would drop a fixed past date."""

import logging
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from api.range import jst_today
from pipeline.clickhouse import LIVE_TABLE, insert_updates
from pipeline.promote import PromotionIncomplete, promote_closed_days

_JST = ZoneInfo("Asia/Tokyo")


def _at(day, hh, mm=0, ss=0):
    return datetime.combine(day, time(hh, mm, ss), tzinfo=_JST).astimezone(timezone.utc)


def _row(file_name, at, trip="T1", seq=1, delay=60):
    return (file_name, at, trip, "平日", "12:00", "R1", seq, delay)


def _files(ch_client, table, agency_id):
    return sorted(
        r[0]
        for r in ch_client.query(
            f"SELECT DISTINCT file_name FROM {table} WHERE agency_id = {{a:UInt16}}", parameters={"a": agency_id}
        ).result_rows
    )


def _count(ch_client, table, agency_id):
    return ch_client.query(
        f"SELECT count() FROM {table} WHERE agency_id = {{a:UInt16}}", parameters={"a": agency_id}
    ).result_rows[0][0]


def test_copies_closed_days_and_never_today(pg_conn, ch_client, agency_id):
    today = jst_today()
    yday = today - timedelta(days=1)
    insert_updates(
        ch_client,
        agency_id,
        [
            _row("oracle/y/a.pb", _at(yday, 12), "A", 1),
            _row("oracle/y/a.pb", _at(yday, 12), "A", 2),
            _row("oracle/y/b.pb", _at(yday, 23, 59, 59), "B"),
            _row("oracle/t/c.pb", _at(today, 0, 0, 0), "C"),  # JST midnight belongs to today
        ],
        table=LIVE_TABLE,
    )

    assert promote_closed_days(agency_id, pg_conn, ch_client) == 3
    assert _files(ch_client, "updates", agency_id) == ["oracle/y/a.pb", "oracle/y/b.pb"]
    assert _count(ch_client, LIVE_TABLE, agency_id) == 4  # the live table is never drained


def test_a_rerun_copies_nothing_twice(pg_conn, ch_client, agency_id):
    yday = jst_today() - timedelta(days=1)
    insert_updates(ch_client, agency_id, [_row("oracle/y/a.pb", _at(yday, 12))], table=LIVE_TABLE)
    assert promote_closed_days(agency_id, pg_conn, ch_client) == 1
    assert promote_closed_days(agency_id, pg_conn, ch_client) == 0
    assert _count(ch_client, "updates", agency_id) == 1


def test_a_poll_landing_after_its_day_was_promoted_follows_on_the_next_run(pg_conn, ch_client, agency_id):
    yday = jst_today() - timedelta(days=1)
    insert_updates(ch_client, agency_id, [_row("oracle/y/a.pb", _at(yday, 12))], table=LIVE_TABLE)
    promote_closed_days(agency_id, pg_conn, ch_client)
    insert_updates(ch_client, agency_id, [_row("oracle/y/late.pb", _at(yday, 23, 59, 59), "L")], table=LIVE_TABLE)
    assert promote_closed_days(agency_id, pg_conn, ch_client) == 1
    assert _files(ch_client, "updates", agency_id) == ["oracle/y/a.pb", "oracle/y/late.pb"]


def test_files_already_in_updates_for_the_day_are_skipped(pg_conn, ch_client, agency_id):
    """The day the split ships: its earlier polls already sit in updates."""
    yday = jst_today() - timedelta(days=1)
    insert_updates(ch_client, agency_id, [_row("oracle/y/pre.pb", _at(yday, 9))])
    insert_updates(
        ch_client,
        agency_id,
        [
            _row("oracle/y/pre.pb", _at(yday, 9)),
            _row("oracle/y/post.pb", _at(yday, 18)),
        ],
        table=LIVE_TABLE,
    )
    assert promote_closed_days(agency_id, pg_conn, ch_client) == 1
    assert _count(ch_client, "updates", agency_id) == 2


def test_a_day_holding_archive_rows_is_not_promoted(pg_conn, ch_client, agency_id, caplog):
    yday = jst_today() - timedelta(days=1)
    insert_updates(ch_client, agency_id, [_row(f"{yday:%Y%m%d}/TripUpdate_120000.pb", _at(yday, 12))])
    insert_updates(ch_client, agency_id, [_row("oracle/y/a.pb", _at(yday, 13))], table=LIVE_TABLE)
    with caplog.at_level(logging.WARNING, logger="pipeline.promote"):
        assert promote_closed_days(agency_id, pg_conn, ch_client) == 0
    assert _files(ch_client, "updates", agency_id) == [f"{yday:%Y%m%d}/TripUpdate_120000.pb"]
    assert "archive rows" in caplog.text


def test_another_agencys_live_rows_stay_put(pg_conn, ch_client, agency_id):
    yday = jst_today() - timedelta(days=1)
    insert_updates(ch_client, agency_id + 1000, [_row("oracle/y/a.pb", _at(yday, 12))], table=LIVE_TABLE)
    assert promote_closed_days(agency_id, pg_conn, ch_client) == 0
    assert _count(ch_client, "updates", agency_id + 1000) == 0


def test_a_copy_that_falls_short_is_raised_not_skipped(pg_conn, ch_client, agency_id):
    class _DropsInserts:
        def __init__(self, inner):
            self._inner = inner

        def command(self, *_a, **_k):
            return None

        def __getattr__(self, name):
            return getattr(self._inner, name)

    yday = jst_today() - timedelta(days=1)
    insert_updates(ch_client, agency_id, [_row("oracle/y/a.pb", _at(yday, 12))], table=LIVE_TABLE)
    with pytest.raises(PromotionIncomplete):
        promote_closed_days(agency_id, pg_conn, _DropsInserts(ch_client))
