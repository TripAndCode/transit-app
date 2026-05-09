"""Shared protobuf + utility helpers for ingest strategies.

Lifted verbatim (with one path-aware change to _ts) from pipeline/ingest.py
so the byte-identical Aomori behaviour is preserved when ingest.py becomes
a router.
"""

import re
import struct
from datetime import datetime


# ── varint protobuf decoder (no external dependencies) ────────────────────────


def _read_varint(data, pos):
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
    length, pos = _read_varint(data, pos)
    return data[pos : pos + length], pos + length


def _fields(data):
    pos = 0
    f = {}
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
    return b.decode("utf-8") if isinstance(b, bytes) else b


# ── captured_at derivation ────────────────────────────────────────────────────


def _ts(date_str: str, pb_name: str) -> str:
    """Combine archive date dir + pb filename into an ISO timestamp.

    Same semantics as the original pipeline.ingest._ts: looks for
    `_HHMMSS.pb` in the filename and pairs it with date_str (YYYYMMDD).
    Falls back to plain date or 'now' if the format doesn't match.
    """
    m = re.search(r"_(\d{6})\.pb$", pb_name, re.IGNORECASE)
    if m and len(date_str) == 8:
        try:
            return datetime.strptime(date_str + m.group(1), "%Y%m%d%H%M%S").isoformat()
        except Exception:
            pass
    try:
        return datetime.strptime(date_str, "%Y%m%d").isoformat()
    except Exception:
        return datetime.now().isoformat()


# ── INSERT shape shared by all ingest strategies ──────────────────────────────


UPDATE_INSERT_SQL = """
    INSERT INTO updates
      (agency_id, file_name, captured_at, trip_id, service_type, scheduled_time,
       route_code, stop_sequence, dep_delay)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT DO NOTHING
"""
