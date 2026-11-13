"""The SQL service date and the Python one are the same function.

They are written twice (ClickHouse for the queries, Python for the callers that
hold a timestamp), so this holds them to each other for every minute of two
days across the cutover and the UTC/JST boundaries.
"""

import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from pipeline.db import timely_reading, timely_reading_ch
from pipeline.service_day import (
    departure_lag,
    departure_lag_ch,
    filed_date_ch,
    filed_date_of,
    service_date_ch,
    service_date_of,
    service_day_offset,
    service_day_offset_ch,
)

pytestmark = pytest.mark.skipif(os.environ.get("RUN_CH_INTEGRATION") != "1", reason="requires `make ch-test`")

JST = ZoneInfo("Asia/Tokyo")
_START = datetime(2026, 3, 6, 0, 0, tzinfo=timezone.utc)
_MINUTES = 48 * 60


def test_the_sql_service_date_matches_the_python_one_for_every_minute(ch_client):
    rows = ch_client.query(
        "SELECT t, " + service_date_ch("t") + " AS d FROM ("
        "SELECT toDateTime64('2026-03-06 00:00:00', 0, 'UTC') + INTERVAL number MINUTE AS t "
        f"FROM numbers({_MINUTES}))"
    ).result_rows
    assert len(rows) == _MINUTES
    # The driver returns the UTC instants as naive datetimes; reading them as
    # local time would compare against the wrong moment.
    moments = [(t.replace(tzinfo=timezone.utc), d) for t, d in rows]
    mismatches = [(t, d) for t, d in moments if d != service_date_of(t)]
    assert mismatches == []
    # The sweep really crosses the boundary: more than one service date appears.
    assert len({d for _, d in moments}) >= 2
    assert moments[0][0] == _START
    assert moments[-1][0] == _START + timedelta(minutes=_MINUTES - 1)


def test_the_sql_service_day_offset_matches_the_python_one_for_every_minute_around_the_clock(ch_client):
    # 00:00 through 27:59: the un-extended hours before the cutover, the day,
    # and GTFS's extended hours past 24:00.
    rows = ch_client.query(
        f"SELECT s, {service_day_offset_ch('s')} FROM (SELECT toInt32(number * 60) AS s FROM numbers({28 * 60}))"
    ).result_rows
    assert len(rows) == 28 * 60
    assert [(s, got) for s, got in rows if got != service_day_offset(s)] == []


def test_the_sql_service_day_offset_keeps_a_missing_value_missing(ch_client):
    rows = ch_client.query(f"SELECT {service_day_offset_ch('CAST(NULL AS Nullable(Int32))')}").result_rows
    assert rows == [(None,)]


def test_the_sql_filed_date_matches_the_python_one_around_the_clock(ch_client):
    """Every 7th minute of two days against stored schedules on and off the
    hour, past 24:00, with none, and as an aomori_regex ``HH:MM`` string."""
    seconds = [0, 600, 14340, 14400, 14700, 21600, 43200, 85800, 86400, 99960, 100800, 108000]
    rows = ch_client.query(
        f"SELECT captured_at, scheduled_sec, scheduled_time, {filed_date_ch('')} FROM ("
        "SELECT toDateTime64('2026-03-05 15:00:00', 0, 'UTC') + INTERVAL number * 7 MINUTE AS captured_at, "
        f"arrayJoin(CAST(arrayConcat([NULL], {seconds}) AS Array(Nullable(Int32)))) AS scheduled_sec, "
        "CAST(NULL AS Nullable(String)) AS scheduled_time FROM numbers(412) "
        "UNION ALL SELECT toDateTime64('2026-03-05 15:00:00', 0, 'UTC') + INTERVAL number * 7 MINUTE, "
        "CAST(NULL AS Nullable(Int32)), arrayJoin(['04:05', '23:59', '00:40']) FROM numbers(412))"
    ).result_rows
    assert len(rows) == 412 * (len(seconds) + 4)
    mismatches = []
    for captured, sec, text, filed in rows:
        captured = captured.replace(tzinfo=timezone.utc)
        if sec is None and text is not None:
            hours, minutes = text.split(":")
            sec = int(hours) * 3600 + int(minutes) * 60
        if filed != filed_date_of(captured, sec):
            mismatches.append((captured, sec, filed))
    assert mismatches == []
    assert any(filed != service_date_of(c.replace(tzinfo=timezone.utc)) for c, _s, _t, filed in rows)


def test_the_sql_departure_lag_and_timeliness_match_the_python_ones_around_the_clock(ch_client):
    """Every 7th minute of two days against stored departures on and off the
    hour, past 24:00, un-extended after midnight, and with none beside an
    aomori_regex ``HH:MM`` string, which no lag is read from."""
    seconds = [600, 14340, 14400, 21600, 33000, 43200, 85800, 91800, 97200, 108000]
    rows = ch_client.query(
        f"SELECT u.captured_at, u.scheduled_sec, {departure_lag_ch('u.')}, {timely_reading_ch()} FROM ("
        "SELECT toDateTime64('2026-03-05 15:00:00', 0, 'UTC') + INTERVAL number * 7 MINUTE AS captured_at, "
        f"arrayJoin(CAST(arrayConcat([NULL], {seconds}) AS Array(Nullable(Int32)))) AS scheduled_sec, "
        "if(scheduled_sec IS NULL, '09:10', CAST(NULL AS Nullable(String))) AS scheduled_time FROM numbers(412)) AS u"
    ).result_rows
    assert len(rows) == 412 * (len(seconds) + 1)
    mismatches = []
    for captured, sec, lag, timely in rows:
        captured = captured.replace(tzinfo=timezone.utc)
        if (lag, bool(timely)) != (departure_lag(captured, sec), timely_reading(captured, sec)):
            mismatches.append((captured, sec, lag, timely))
    assert mismatches == []
    assert {bool(timely) for *_rest, timely in rows} == {True, False}


@pytest.mark.parametrize(
    ("jst", "scheduled_sec", "timely"),
    [
        # 09:10, read up to 130 minutes after it.
        ("2026-03-06 11:20:00", 33000, True),
        ("2026-03-06 11:20:01", 33000, False),
        # 25:30, the next calendar day's 01:30, on its trip's service day.
        ("2026-03-07 03:40:00", 91800, True),
        ("2026-03-07 03:40:01", 91800, False),
        # 27:00, read after the cutover and filed back under its trip's day.
        ("2026-03-07 05:10:00", 97200, True),
        ("2026-03-07 05:10:01", 97200, False),
        # An un-extended 00:30 is the service day's 24:30.
        ("2026-03-07 02:40:00", 1800, True),
        ("2026-03-07 02:40:01", 1800, False),
        # No stored departure of the stop: kept whenever captured.
        ("2026-03-06 23:59:00", None, True),
    ],
)
def test_a_reading_is_timely_until_130_minutes_after_its_stops_departure(ch_client, jst, scheduled_sec, timely):
    captured = datetime.fromisoformat(jst).replace(tzinfo=JST).astimezone(timezone.utc)
    rows = ch_client.query(
        f"SELECT {timely_reading_ch()} FROM (SELECT toDateTime64({{at:String}}, 0, 'UTC') AS captured_at, "
        "CAST({sec:Nullable(Int32)} AS Nullable(Int32)) AS scheduled_sec, "
        "CAST(NULL AS Nullable(String)) AS scheduled_time) AS u",
        parameters={"at": captured.strftime("%Y-%m-%d %H:%M:%S"), "sec": scheduled_sec},
    ).result_rows
    assert (bool(rows[0][0]), timely_reading(captured, scheduled_sec)) == (timely, timely)
