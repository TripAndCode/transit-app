"""Hand-encoded GTFS-RT messages for tests that need a feed's exact wire bytes."""

import struct


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


def _float(field: int, value: float) -> bytes:
    """A single-precision float field, fixed 32 bits on the wire, rounded to the nearest float32."""
    return _varint((field << 3) | 5) + struct.pack("<f", value)


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
    schedule_relationship: int | None = None,
) -> bytes:
    """A StopTimeUpdate; every field is left off the wire when None.
    ``schedule_relationship`` is GTFS-RT's enum value: SCHEDULED 0, SKIPPED 1,
    NO_DATA 2."""
    body = b""
    if stop_sequence is not None:
        body += _uint(1, stop_sequence)
    if arrival_delay is not None:
        body += _len_delimited(2, _int32(1, arrival_delay))
    if departure_delay is not None:
        body += _len_delimited(3, _int32(1, departure_delay))
    if stop_id is not None:
        body += _string(4, stop_id)
    if schedule_relationship is not None:
        body += _uint(5, schedule_relationship)
    return body


def trip_update(
    trip_id: str,
    *stop_time_updates: bytes,
    route_id: str | None = None,
    start_date: str | None = None,
    schedule_relationship: int | None = None,
    vehicle: bytes | None = None,
) -> bytes:
    """A TripUpdate whose TripDescriptor carries trip_id, plus route_id,
    start_date ("YYYYMMDD", the trip's service date) and schedule_relationship
    (GTFS-RT's enum value: SCHEDULED 0, CANCELED 3) when given, and the
    TripUpdate's own `vehicle` (field 3, a :func:`vehicle_descriptor`)."""
    trip = _string(1, trip_id)
    if start_date is not None:
        trip += _string(3, start_date)
    if schedule_relationship is not None:
        trip += _uint(4, schedule_relationship)
    if route_id is not None:
        trip += _string(5, route_id)
    body = _len_delimited(1, trip) + b"".join(_len_delimited(2, stu) for stu in stop_time_updates)
    return body if vehicle is None else body + _len_delimited(3, vehicle)


def vehicle_descriptor(vehicle_id: str, *, label: str | None = None, license_plate: str | None = None) -> bytes:
    """A VehicleDescriptor: id, label and license_plate (fields 1 to 3)."""
    body = _string(1, vehicle_id)
    if label is not None:
        body += _string(2, label)
    if license_plate is not None:
        body += _string(3, license_plate)
    return body


def vehicle_position_entity(
    trip_id: str,
    vehicle: bytes,
    *,
    entity_id: str | None = None,
    start_date: str | None = None,
    position: tuple[float, float] | None = None,
    timestamp: int | None = None,
) -> bytes:
    """A FeedEntity (FeedMessage field 2), with its `id` (field 1) when
    given, holding a VehiclePosition (entity field 4). The VehiclePosition
    carries its trip, *trip_id* with start_date ("YYYYMMDD") when given, its
    `position` (field 2: latitude, longitude) and `timestamp` (field 5, POSIX
    seconds) when given, and its `vehicle` (field 8)."""
    trip = _string(1, trip_id)
    if start_date is not None:
        trip += _string(3, start_date)
    body = _len_delimited(1, trip)
    if position is not None:
        latitude, longitude = position
        body += _len_delimited(2, _float(1, latitude) + _float(2, longitude))
    if timestamp is not None:
        body += _uint(5, timestamp)
    body += _len_delimited(8, vehicle)
    entity = b"" if entity_id is None else _string(1, entity_id)
    return _len_delimited(2, entity + _len_delimited(4, body))


def vehicle_position_feed(*entities: bytes, timestamp: int) -> bytes:
    """A FeedMessage of :func:`vehicle_position_entity` entities under a
    FeedHeader stamped *timestamp* (POSIX seconds)."""
    return header_only_feed(timestamp) + b"".join(entities)


def trip_update_feed(*trip_updates: bytes, timestamp: int | None = None) -> bytes:
    """A FeedMessage with one FeedEntity per TripUpdate. It has a FeedHeader
    only when *timestamp* (POSIX seconds) is given, as a real poll does."""
    header = header_only_feed(timestamp) if timestamp is not None else b""
    return header + b"".join(_len_delimited(2, _len_delimited(3, tu)) for tu in trip_updates)
