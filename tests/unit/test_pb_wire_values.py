"""Wire values a GTFS-RT feed may legally send must decode into something the
`updates` columns can hold: a negative int32 delay, and a StopTimeUpdate
that carries no stop_sequence."""

from unittest.mock import MagicMock

import pytest

from pipeline.strategies import aomori_regex
from pipeline.strategies._pb import _fields, _int32, _uint16
from pipeline.strategies.static_join import _decode_rows
from tests.fixtures.gtfs_rt import stop_time_update, trip_update, trip_update_feed


def _raw_departure_delay(delay: int) -> int:
    return _fields(_fields(stop_time_update(departure_delay=delay))[3][0])[1][0]


@pytest.mark.parametrize("delay", [-30, -1, 0, 45, -(2**31), 2**31 - 1])
def test_int32_reads_the_signed_value_protobuf_encoded(delay):
    assert _int32(_raw_departure_delay(delay)) == delay


def test_a_negative_delay_arrives_as_a_64_bit_varint():
    # The case _int32 exists for: the raw varint is far outside Int32.
    assert _raw_departure_delay(-30) == 2**64 - 30


@pytest.mark.parametrize("value", [None, b"\x01"])
def test_int32_reads_a_missing_or_non_varint_field_as_absent(value):
    assert _int32(value) is None


@pytest.mark.parametrize("value, expected", [(0, 0), (1, 1), (65535, 65535), (65536, None), (None, None), (b"", None)])
def test_uint16_keeps_only_values_the_column_can_hold(value, expected):
    assert _uint16(value) == expected


def test_static_join_decodes_signed_delays_and_a_missing_stop_sequence():
    pb = trip_update_feed(
        trip_update(
            "uuid-A",
            stop_time_update(1, departure_delay=-30, arrival_delay=-45),
            stop_time_update(stop_id="S2", departure_delay=60),
            route_id="R1",
        )
    )
    rows = list(_decode_rows(pb))
    assert [(r[2], r[3], r[5]) for r in rows] == [(1, -30, -45), (None, 60, None)]


def _aomori_conn():
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value.fetchone.return_value = None
    return conn


def test_aomori_keeps_negative_delays_and_drops_updates_without_stop_sequence():
    pb = trip_update_feed(
        trip_update(
            "平日_12時00分_系統1",
            stop_time_update(1, departure_delay=-30),
            stop_time_update(departure_delay=60),
            stop_time_update(70000, departure_delay=60),
            stop_time_update(2, departure_delay=45),
        )
    )
    rows = aomori_regex.parse_feed(pb, "2026-05-09T12:00:00+09:00", "f.pb", 1, _aomori_conn())
    assert [(r[6], r[7]) for r in rows] == [(1, -30), (2, 45)]
