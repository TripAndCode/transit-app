"""``pipeline.ingest.ingest_live_payload``: bytes that cannot be decoded raise
PayloadDecodeError, which the collector endpoint answers with a 422 instead
of a retried 502; a failure of anything else passes through unchanged."""

import struct

import pytest

import pipeline.ingest as ingest
from pipeline.ingest import PayloadDecodeError


class _Strategy:
    def __init__(self, exc):
        self.exc = exc

    def parse_feed(self, *_args):
        raise self.exc


def _ingest(monkeypatch, exc):
    monkeypatch.setattr(ingest, "get_ingest_strategy", lambda _name: _Strategy(exc))
    monkeypatch.setattr(ingest, "recent_file_name_exists", lambda *_a, **_k: False)
    return ingest.ingest_live_payload(1, b"\xff", "2026-10-01T00:00:00+00:00", "oracle/x", None, None, "static_join")


@pytest.mark.parametrize("exc", [IndexError("index out of range"), struct.error("unpack"), ValueError("bad varint")])
def test_bytes_that_cannot_be_decoded_raise_payload_decode_error(monkeypatch, exc):
    with pytest.raises(PayloadDecodeError, match="oracle/x"):
        _ingest(monkeypatch, exc)


def test_any_other_failure_passes_through(monkeypatch):
    with pytest.raises(ConnectionError):
        _ingest(monkeypatch, ConnectionError("postgres went away"))
