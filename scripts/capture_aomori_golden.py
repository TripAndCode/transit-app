"""One-shot: parse tests/fixtures/aomori_sample.pb with the current pre-refactor
parse_pb and write the resulting rows as JSON to tests/fixtures/aomori_golden.json.

Run once on the pre-refactor commit. The output is checked into git and
becomes the regression target for the post-refactor code.

Usage:
    python scripts/capture_aomori_golden.py [--date YYYYMMDD]

The --date flag (default "20260509") controls both the captured_at timestamp
and the file_name prefix, so snapshots taken on different dates remain
deterministic when committed.
"""

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pipeline.ingest import _ts, parse_pb  # noqa: E402  (sys.path injection above)


def main() -> None:
    """Parse aomori_sample.pb and write golden JSON to tests/fixtures."""
    parser = argparse.ArgumentParser(description="Capture Aomori golden fixture from aomori_sample.pb")
    parser.add_argument(
        "--date",
        default="20260509",
        help="Date string YYYYMMDD used for captured_at and file_name (default: 20260509)",
    )
    args = parser.parse_args()

    pb_path = ROOT / "tests" / "fixtures" / "aomori_sample.pb"
    raw = pb_path.read_bytes()

    # Mimic ingest()'s captured_at derivation. Use a fixed date so the snapshot
    # is reproducible regardless of when the script is run.
    captured_at = _ts(args.date, "TripUpdate_120000.pb")
    rows = parse_pb(raw, captured_at, f"{args.date}/TripUpdate_120000.pb")

    # parse_pb returns 12-tuples; serialize to JSON arrays for stability.
    serialized = [list(row) for row in rows]

    out = ROOT / "tests" / "fixtures" / "aomori_golden.json"
    out.write_text(json.dumps(serialized, ensure_ascii=False, indent=2))
    print(f"Wrote {len(serialized)} rows to {out}")


if __name__ == "__main__":
    main()
