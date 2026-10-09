"""Hand-encoded GTFS-RT messages for tests that need a feed's exact wire bytes."""


def _varint(n: int) -> bytes:
    out = bytearray()
    while n > 0x7F:
        out.append((n & 0x7F) | 0x80)
        n >>= 7
    out.append(n)
    return bytes(out)


def _int32(field: int, n: int) -> bytes:
    """An int32 field. Protobuf sign-extends a negative int32 to 64 bits, so
    -30 goes on the wire as a 10-byte varint."""
    return _varint(field << 3) + _varint(n & 0xFFFF_FFFF_FFFF_FFFF)


def _uint(field: int, n: int) -> bytes:
    return _varint(field << 3) + _varint(n)


def _len_delimited(field: int, body: bytes) -> bytes:
    return _varint((field << 3) | 2) + _varint(len(body)) + body


def _string(field: int, value: str) -> bytes:
    return _len_delimited(field, value.encode("utf-8"))


def header_only_feed(timestamp: int | None) -> bytes:
    """A FeedMessage holding only its FeedHeader: version "2.0", plus field 3
    `timestamp` (POSIX seconds) when given."""
    header = b"\x0a\x032.0"
    if timestamp is not None:
        header += b"\x18" + _varint(timestamp)
    return b"\x0a" + _varint(len(header)) + header


def stop_time_update(
    stop_sequence: int | None = None,
    *,
    departure_delay: int | None = None,
    arrival_delay: int | None = None,
    stop_id: str | None = None,
) -> bytes:
    """A StopTimeUpdate; every field is left off the wire when None."""
    body = b""
    if stop_sequence is not None:
        body += _uint(1, stop_sequence)
    if arrival_delay is not None:
        body += _len_delimited(2, _int32(1, arrival_delay))
    if departure_delay is not None:
        body += _len_delimited(3, _int32(1, departure_delay))
    if stop_id is not None:
        body += _string(4, stop_id)
    return body


def trip_update(trip_id: str, *stop_time_updates: bytes, route_id: str | None = None) -> bytes:
    """A TripUpdate whose TripDescriptor carries trip_id, plus route_id when given."""
    trip = _string(1, trip_id)
    if route_id is not None:
        trip += _string(5, route_id)
    return _len_delimited(1, trip) + b"".join(_len_delimited(2, stu) for stu in stop_time_updates)


def trip_update_feed(*trip_updates: bytes) -> bytes:
    """A FeedMessage with one FeedEntity per TripUpdate and no header."""
    return b"".join(_len_delimited(2, _len_delimited(3, tu)) for tu in trip_updates)
