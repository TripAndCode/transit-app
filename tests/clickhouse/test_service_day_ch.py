"""The SQL service date and the Python one are the same function.

They are written twice (ClickHouse for the queries, Python for the callers that
hold a timestamp), so this holds them to each other for every minute of two
days across the cutover and the UTC/JST boundaries.
"""

import os
from datetime import datetime, timedelta, timezone

import pytest

from pipeline.service_day import service_date_ch, service_date_of

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
