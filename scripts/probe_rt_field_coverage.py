"""Run the per-field coverage check against a GTFS-RT feed.

Fetches (or reads a locally-captured) trip_updates protobuf and reports, per
optional field, what fraction of stop_time_updates carry it -- the same
check ``tests/pipeline/test_static_join.py::test_static_join_per_op``
already applies to agencies 8/9/10's captured fixtures, whose confirmed
coverage ``pipeline/reports/service_delivered.py`` and
``pipeline/reports/dwell_run.py`` both trust via a blanket
``ingest_strategy == 'static_join'`` check -- not a live per-agency check.

Run this against a NEW agency's live feed before assigning it
``ingest_strategy=static_join`` in ``agencies.csv`` and trusting that its
optional fields behave like an already-confirmed agency's: a static_join
agency whose feed doesn't actually send schedule_relationship_*/arr_delay
would otherwise silently misreport through those two features (e.g. reading
a permanently-NULL schedule_relationship_trip as "confirmed zero
cancellations" instead of "not available") until this script -- or the
equivalent check against enough live polls -- confirms or refutes the
assumption.

Usage:
    poetry run python scripts/probe_rt_field_coverage.py --url <feed_url>
    poetry run python scripts/probe_rt_field_coverage.py --file <path/to/trip_updates.bin>

``--url`` requires outbound network access to the feed's host (this script
does not need a configured `agencies` row -- it decodes the raw feed
directly, independent of any DB state). ``--file`` is for a sample already
saved locally, e.g. a capture made by a session that does have network
access, or a fixture under ``tests/fixtures/``.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pipeline.strategies.static_join import field_coverage  # noqa: E402  (sys.path injection above)
from pipeline.url_guard import FeedURLError, safe_urlopen  # noqa: E402

# Coverage thresholds tests/pipeline/test_static_join.py::test_static_join_per_op
# already confirms for agencies 8/9/10 -- mirrored here so this script's report
# explicitly says whether a new feed matches, rather than leaving a human to
# eyeball raw fractions against a threshold living only in a test file.
_NEAR_UNIVERSAL_MIN = 0.99
_ARR_DELAY_SPARSE_RANGE = (0.0, 0.5)  # exclusive lower, exclusive upper


def _fetch(url: str) -> bytes:
    with safe_urlopen(url, timeout=30) as resp:
        return resp.read()


def _assess(cov: dict) -> dict:
    """Compare cov's fractions against the confirmed-agency thresholds.

    Returns cov augmented with a "matches_confirmed_agencies" bool per
    coverage field, or an explicit note when there's nothing to assess.
    """
    if cov["stop_time_updates"] == 0:
        return {**cov, "assessment": "no stop_time_updates decoded -- feed empty, malformed, or wrong shape"}
    lo, hi = _ARR_DELAY_SPARSE_RANGE
    checks = {
        "stop_id_coverage": cov["stop_id_coverage"] >= _NEAR_UNIVERSAL_MIN,
        "schedule_relationship_trip_coverage": cov["schedule_relationship_trip_coverage"] >= _NEAR_UNIVERSAL_MIN,
        "schedule_relationship_stop_coverage": cov["schedule_relationship_stop_coverage"] >= _NEAR_UNIVERSAL_MIN,
        "arr_delay_coverage": lo < cov["arr_delay_coverage"] < hi,
        "feed_timestamp": cov["feed_timestamp"] is not None and cov["feed_timestamp"] > 1_600_000_000,
    }
    return {**cov, "matches_confirmed_agencies": checks}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--url", help="Live GTFS-RT trip_updates feed URL to fetch and probe")
    src.add_argument("--file", help="Path to a locally-captured trip_updates protobuf file")
    args = parser.parse_args()

    if args.url:
        try:
            raw = _fetch(args.url)
        except FeedURLError as e:
            print(f"BLOCKED: {e}", file=sys.stderr)
            sys.exit(1)
        except OSError as e:
            print(f"FETCH FAILED: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        try:
            raw = pathlib.Path(args.file).read_bytes()
        except OSError as e:
            print(f"READ FAILED: {e}", file=sys.stderr)
            sys.exit(1)

    cov = field_coverage(raw)
    print(json.dumps(_assess(cov), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
