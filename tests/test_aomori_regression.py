"""Aomori regression-lock test.

Runs parse_pb on the captured fixture and asserts byte-identical output
against tests/fixtures/aomori_golden.json. This test must pass on every
commit from now on; if a refactor changes Aomori output, the test fails
and the refactor is rejected.
"""

import json
import pathlib

from pipeline.ingest import parse_pb, _ts

FIX_DIR = pathlib.Path(__file__).parent / "fixtures"


def test_aomori_parse_pb_matches_golden():
    raw = (FIX_DIR / "aomori_sample.pb").read_bytes()
    captured_at = _ts("20260509", "TripUpdate_120000.pb")

    rows = parse_pb(raw, captured_at, "20260509/TripUpdate_120000.pb")
    actual = [list(r) for r in rows]

    expected = json.loads((FIX_DIR / "aomori_golden.json").read_text())

    assert actual == expected, (
        "Aomori parse_pb output diverged from golden. "
        "If this is intentional, regenerate via scripts/capture_aomori_golden.py."
    )
