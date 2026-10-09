"""Hand-encoded GTFS-RT messages for tests that only need a feed's header."""


def _varint(n: int) -> bytes:
    out = bytearray()
    while n > 0x7F:
        out.append((n & 0x7F) | 0x80)
        n >>= 7
    out.append(n)
    return bytes(out)


def header_only_feed(timestamp: int | None) -> bytes:
    """A FeedMessage holding only its FeedHeader: version "2.0", plus field 3
    `timestamp` (POSIX seconds) when given."""
    header = b"\x0a\x032.0"
    if timestamp is not None:
        header += b"\x18" + _varint(timestamp)
    return b"\x0a" + _varint(len(header)) + header
