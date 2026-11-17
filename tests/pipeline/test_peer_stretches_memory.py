"""The peer-stretch query stays within memory however many outages a batch
holds.

A batch merges every outage whose scan overlaps the last, so a feed that
drops out every day or two of a month puts all of that month's outages in one
batch, read against every other agency's polls. The query must not give each
poll a row of all the batch's outages, or such a month outgrows the server's
memory.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from datetime import UTC, datetime, timedelta
from itertools import pairwise

from pipeline import feed_quality, poll_outages
from pipeline.clickhouse import UPDATES_TABLE

START = datetime(2026, 4, 6, tzinfo=UTC)
POLLS = 20_000
POLL_EVERY_SEC = 30
# The last polls of every run are missed, a gap longer than the floor.
RUN = 500
MISSED = 20
OUTAGES = 1_000
MEMORY_CAP = 100_000_000


def test_a_batch_of_many_outages_fits_a_small_memory_cap(ch_client, agency_id):
    start = int(START.timestamp())
    ch_client.command(
        f"INSERT INTO {UPDATES_TABLE} (agency_id, captured_at, file_name, trip_id, stop_sequence) "
        f"SELECT {agency_id}, toDateTime64({start} + number * {POLL_EVERY_SEC}, 0, 'UTC'), "
        f"concat('live_', toString(number)), 'T1', 1 "
        f"FROM numbers({POLLS}) WHERE number % {RUN} < {RUN - MISSED}"
    )
    instants = [start + poll * POLL_EVERY_SEC for poll in range(POLLS) if poll % RUN < RUN - MISSED]
    # Each outage's instants reach into the next one's, as two outages
    # closer than twice the peer reach do, so a poll can fall in two.
    span = POLLS * POLL_EVERY_SEC // OUTAGES
    los = [start + outage * span for outage in range(OUTAGES)]
    his = [lo + 2 * span for lo in los]
    result = ch_client.query(
        feed_quality.PEER_STRETCHES_CH_SQL,
        parameters={
            "peers": [agency_id],
            "los": los,
            "his": his,
            "floor": poll_outages.OUTAGE_FLOOR_SEC,
            "scan_from": START,
            "scan_to": START + timedelta(seconds=POLLS * POLL_EVERY_SEC),
        },
        settings={"max_memory_usage": MEMORY_CAP},
    )
    expected = {}
    for outage, (lo, hi) in enumerate(zip(los, his, strict=True)):
        polled = instants[bisect_left(instants, lo) : bisect_right(instants, hi)]
        if polled:
            expected[outage] = [(x, y) for x, y in pairwise(polled) if y - x > poll_outages.OUTAGE_FLOOR_SEC]
    found = {int(outage): list(zip(starts, ends, strict=True)) for outage, starts, ends in result.result_rows}
    assert any(expected.values())
    assert found == expected
