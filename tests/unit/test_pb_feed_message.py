"""`is_feed_message`: telling a GTFS-RT FeedMessage that happens to carry
nothing from a body that is not a feed at all.

`_fields` is lenient on purpose (an ingest keeps what it can of a damaged
poll), so on an HTML page or a truncated body it returns an empty or partial
parse that looks exactly like a quiet feed. A FeedMessage always carries its
FeedHeader, and the header its gtfs_realtime_version, so their absence, or a
body that does not parse to its end, means the bytes are not a feed.
"""

import pathlib

import pytest

from pipeline.strategies._pb import is_feed_message
from tests.fixtures.gtfs_rt import header_only_feed, stop_time_update, trip_update, trip_update_feed

_FIXTURES = pathlib.Path(__file__).parents[1] / "fixtures"


@pytest.mark.parametrize("name", ["hiroden_tu.bin", "hirobus_tu.bin", "hirokoh_tu.bin", "aomori_sample.pb"])
def test_a_real_capture_is_a_feed(name):
    assert is_feed_message((_FIXTURES / name).read_bytes())


def test_an_overnight_capture_with_no_updates_is_still_a_feed():
    assert is_feed_message((_FIXTURES / "geiyo_tu.bin").read_bytes())


@pytest.mark.parametrize("timestamp", [None, 1_793_050_938])
def test_a_header_with_nothing_after_it_is_a_feed(timestamp):
    assert is_feed_message(header_only_feed(timestamp))


def test_a_header_followed_by_entities_is_a_feed():
    body = header_only_feed(1_793_050_938) + trip_update_feed(
        trip_update("t1", stop_time_update(1, departure_delay=30, stop_id="s1"))
    )
    assert is_feed_message(body)


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"<!DOCTYPE html><html><head><title>503 Service Unavailable</title></head></html>",
        b'{"error": "unauthorized"}',
        b"\x00\x00\x00\x00",
        b"\xff\xff\xff\xff\xff\xff",
    ],
    ids=["empty", "html", "json", "zeros", "garbage"],
)
def test_a_body_that_is_not_protobuf_is_not_a_feed(body):
    assert not is_feed_message(body)


def test_entities_without_a_header_are_not_a_feed():
    assert not is_feed_message(trip_update_feed(trip_update("t1", stop_time_update(1, departure_delay=30))))


def test_a_header_without_its_version_is_not_a_feed():
    # FeedMessage{header: FeedHeader{timestamp: 1}}: the version is required.
    assert not is_feed_message(b"\x0a\x02\x18\x01")


def test_a_truncated_feed_is_not_a_feed():
    full = (_FIXTURES / "hirobus_tu.bin").read_bytes()
    assert not is_feed_message(full[: len(full) // 2])
