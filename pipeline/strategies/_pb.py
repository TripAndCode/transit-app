"""Shared protobuf + utility helpers for ingest strategies.

Lifted verbatim (with one path-aware change to _ts) from pipeline/ingest.py
so the byte-identical Aomori behaviour is preserved when ingest.py becomes
a router.
"""

import re
import struct
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

# Every captured_at this module returns is timezone-aware, never naive:
# clickhouse-connect resolves naive datetimes via the *process-local* host
# timezone when writing DateTime64 columns, so a naive string is only
# correct by accident on a JST-timezone host and silently wrong (9h off) on
# a UTC host such as Railway/Docker/CI.
_JST = ZoneInfo("Asia/Tokyo")

# ── varint protobuf decoder (no external dependencies) ────────────────────────


def _read_varint(data, pos):
    """Decode a protobuf base-128 varint from data starting at pos.

    Returns (value, new_pos).
    """
    result, shift = 0, 0
    while True:
        b = data[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            break
        shift += 7
    return result, pos


def _read_ld(data, pos):
    """Read a length-delimited protobuf field. Returns (bytes_value, new_pos)."""
    length, pos = _read_varint(data, pos)
    return data[pos : pos + length], pos + length


def _fields(data):
    """Parse a protobuf message into a dict of field_number → [value, ...].

    Handles wire types 0 (varint), 1 (64-bit), 2 (length-delimited), and
    5 (32-bit). Unknown wire types terminate parsing early.
    """
    pos = 0
    f: dict[int, list[Any]] = {}
    while pos < len(data):
        try:
            tw, pos = _read_varint(data, pos)
            fn, wt = tw >> 3, tw & 7
            if wt == 0:
                v, pos = _read_varint(data, pos)
                f.setdefault(fn, []).append(v)
            elif wt == 2:
                v, pos = _read_ld(data, pos)
                f.setdefault(fn, []).append(v)
            elif wt == 1:
                v = struct.unpack_from("<Q", data, pos)[0]
                pos += 8
                f.setdefault(fn, []).append(v)
            elif wt == 5:
                v = struct.unpack_from("<I", data, pos)[0]
                pos += 4
                f.setdefault(fn, []).append(v)
            else:
                break
        except Exception:
            break
    return f


def _dec(b):
    """Decode bytes to str, passing through non-bytes values unchanged."""
    return b.decode("utf-8") if isinstance(b, bytes) else b


def decode_feed_timestamp(pb_bytes: bytes):
    """Return the top-level FeedMessage's FeedHeader.timestamp (field 1 ->
    field 3), or None if the message/header/field is absent or undecodable.

    Field 2 on FeedHeader is `incrementality` (an enum, usually 0/1) --
    `timestamp` is field 3. One value per feed message -- every ingest
    strategy shares this same decode rather than each re-implementing it,
    so a future field-number fix only needs to happen once.
    """
    try:
        top = _fields(pb_bytes)
    except Exception:
        return None
    if 1 not in top:
        return None
    header = _fields(top[1][0])
    return header.get(3, [None])[0]


# ── captured_at derivation ────────────────────────────────────────────────────


def archive_captured_at(pb_bytes: bytes, date_str: str, pb_name: str) -> str:
    """captured_at for an archived feed file: the feed's own FeedHeader
    timestamp when it is plausible for the file's name, else the name via
    _ts().

    The header is an absolute POSIX instant. An archive name is a wall-clock
    reading in whatever zone its collector used -- rt-poller.sh names in
    UTC, while _ts() reads names as JST -- so the header wins whenever it
    falls on the name's JST day or the next one, the two days a UTC or JST
    reading of that name can land on. Outside that window (a frozen feed's
    old header, a producer clock far off) the name stands, which also keeps
    every stamp at or after JST midnight of the name's day: the bound
    pipeline.ingest._archive_since gives the already-ingested skip-list.
    """
    feed_ts = decode_feed_timestamp(pb_bytes)
    if isinstance(feed_ts, int) and feed_ts > 0:
        try:
            stamped = datetime.fromtimestamp(feed_ts, tz=_JST)
        except (OverflowError, OSError, ValueError):
            return _ts(date_str, pb_name)
        try:
            name_day = datetime.strptime(date_str, "%Y%m%d").date()
        except ValueError:
            return stamped.isoformat()
        if 0 <= (stamped.date() - name_day).days <= 1:
            return stamped.isoformat()
    return _ts(date_str, pb_name)


def _ts(date_str: str, pb_name: str) -> str:
    """Read archive date dir + pb filename as a JST wall-clock time.

    Looks for `_HHMMSS.pb` in the filename and pairs it with date_str
    (YYYYMMDD). Falls back to plain date or 'now' if the format doesn't
    match. Prefer archive_captured_at(), which only falls back to this when
    the feed has no header timestamp.
    """
    m = re.search(r"_(\d{6})\.pb$", pb_name, re.IGNORECASE)
    if m and len(date_str) == 8:
        try:
            return datetime.strptime(date_str + m.group(1), "%Y%m%d%H%M%S").replace(tzinfo=_JST).isoformat()
        except Exception:
            pass
    try:
        return datetime.strptime(date_str, "%Y%m%d").replace(tzinfo=_JST).isoformat()
    except Exception:
        return datetime.now(_JST).isoformat()
