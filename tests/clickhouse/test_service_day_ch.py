"""The SQL service date and the Python one are the same function.

They are written twice (ClickHouse for the queries, Python for the callers that
hold a timestamp), so this holds them to each other for every minute of two
days across the cutover and the UTC/JST boundaries.
"""

import os
from datetime import datetime, timedelta, timezone

import pytest

from pipeline.service_day import (
    filed_date_ch,
    filed_date_of,
    service_date_ch,
    service_date_of,
    service_day_offset,
    service_day_offset_ch,
)

pytestmark = pytest.mark.skipif(os.environ.get("RUN_CH_INTEGRATION") != "1", reason="requires `make ch-test`")

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
