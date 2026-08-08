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

# Archive filenames/date-dirs encode local Japan time (same assumption the
# whole app makes about `updates.captured_at`). _ts() must attach this
# explicitly rather than returning a naive string: clickhouse-connect
# resolves naive datetimes via the *process-local* host timezone when
# writing DateTime64 columns, so a naive string is only correct by accident
# on a JST-timezone host and silently wrong (9h off) on a UTC host such as
# Railway/Docker/CI. See _ts()'s docstring.
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


# ── captured_at derivation ────────────────────────────────────────────────────


def _ts(date_str: str, pb_name: str) -> str:
    """Combine archive date dir + pb filename into a JST-aware ISO timestamp.

    Same semantics as the original pipeline.ingest._ts: looks for
    `_HHMMSS.pb` in the filename and pairs it with date_str (YYYYMMDD).
    Falls back to plain date or 'now' if the format doesn't match.

    The returned string is always timezone-aware (Asia/Tokyo), never naive:
    archive filenames encode local Japan time, and clickhouse-connect
    resolves a naive datetime using the *host process's* local timezone
    when writing it, not JST — so a naive string here would silently shift
    every archive-ingested row by the host/JST offset (9h on a UTC host).
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

