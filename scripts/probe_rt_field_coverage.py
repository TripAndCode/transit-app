"""Run the per-field coverage check against a GTFS-RT feed, and optionally
record the verdict in the durable ``rt_field_coverage_probes`` registry.

Fetches (or reads a locally-captured) trip_updates protobuf and reports, per
optional field, what fraction of stop_time_updates carry it. With
``--record --agency-id N`` the same verdict is persisted, which is what
makes an agency's ``schedule_relationship_*``/``arr_delay``/``stop_id``-
derived reports available: the read-side gate
(``pipeline.strategies.static_join.rt_field_coverage_confirmed``) consults
that registry, so onboarding a newly-verified feed -- or letting a feed that
regressed fall back to "not available" -- needs a probe run, not a code
change and a deploy.

Run this against a NEW agency's live feed before trusting that its optional
fields behave like an already-verified agency's: a static_join agency whose
feed doesn't actually send schedule_relationship_*/arr_delay would otherwise
silently misreport through those features (e.g. reading a permanently-NULL
schedule_relationship_trip as "confirmed zero cancellations" instead of "not
available").

Probe during active service hours. An empty/overnight capture (zero
stop_time_updates) proves nothing either way and is refused by ``--record``
rather than written as a refutation.

Usage:
    poetry run python scripts/probe_rt_field_coverage.py --url <feed_url>
    poetry run python scripts/probe_rt_field_coverage.py --file <path/to/trip_updates.bin>
    poetry run python scripts/probe_rt_field_coverage.py --url <feed_url> --agency-id 12 --record

``--url`` requires outbound network access to the feed's host. Without
``--record`` this script needs no DB at all (it decodes the raw feed
directly, independent of any DB state), which is what lets it vet a feed
before an `agencies` row for it even exists. ``--file`` is for a sample
already saved locally, e.g. a capture made by a session that does have
network access, or a fixture under ``tests/fixtures/``.

With ``--record``, a ``--url`` probe must be the ``feed_url`` already
stored for ``--agency-id`` or nothing is written; a ``--file`` capture has
no URL to check and is trusted (``_record`` spells out why).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pipeline.strategies.static_join import (  # noqa: E402  (sys.path injection above)
    DEFAULT_PROBE_TTL_DAYS,
    assess_field_coverage,
    field_coverage,
    record_field_coverage_probe,
)
from pipeline.url_guard import FeedURLError, safe_urlopen  # noqa: E402

# A feed_timestamp is a whole-feed field rather than a per-stop_time_update
# one, so it is reported but never recorded in the per-field registry. The
# bound rejects a small integer that parsed as a plausible varint but cannot
# be a real epoch second, which is the usual symptom of decoding something
# that is not a GTFS-RT feed at all.
_MIN_PLAUSIBLE_FEED_TIMESTAMP = 1_600_000_000


def _fetch(url: str) -> bytes:
    with safe_urlopen(url, timeout=30) as resp:
        return resp.read()


def _assess(cov: dict) -> dict:
    """Compare cov's fractions against the confirmed-coverage thresholds.

    Returns cov augmented with a "matches_confirmed_agencies" bool per
    coverage field, or an explicit note when there's nothing to assess. The
    per-field verdicts come from
    `pipeline.strategies.static_join.assess_field_coverage` -- the same call
    `--record` persists -- so this human-facing report can never disagree
    with what gets written to the registry.
    """
    verdicts = assess_field_coverage(cov)
    if verdicts is None:
        return {**cov, "assessment": "no stop_time_updates decoded -- feed empty, malformed, or wrong shape"}
    checks = {f"{field}_coverage": ok for field, ok in verdicts.items()}
    checks["feed_timestamp"] = cov["feed_timestamp"] is not None and (
        cov["feed_timestamp"] > _MIN_PLAUSIBLE_FEED_TIMESTAMP
    )
    return {**cov, "matches_confirmed_agencies": checks}


async def _record(agency_id: int, cov: dict, source_feed: str, ttl_days: int | None, *, probed_url: str | None) -> None:
    """Persist this run's verdict for *agency_id*, after checking the thing
    that was probed really is that agency's feed.

    *source_feed* is recorded as the provenance of the verdict: the feed URL
    for ``--url``, the capture's path for ``--file``. *probed_url* is set
    only in the first case, and then it must equal ``agencies.feed_url``
    exactly or nothing is written: these vendor feeds differ only by an
    operator number in the path, so a mistyped ``--agency-id`` would
    otherwise record one operator's coverage against another's row -- as a
    full set of affirmative verdicts, which is worse than no verdict at all.

    A ``--file`` capture passes ``probed_url=None`` on purpose, not by
    omission: a local path can never equal a feed URL, so there is nothing
    to compare, and vetting a capture taken elsewhere (by a session that had
    network access, or a checked-in fixture) is exactly what that flag is
    for. The operator vouches for which feed it came from, and *source_feed*
    names the capture so the claim stays auditable.
    """
    import asyncpg

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("RECORD FAILED: DATABASE_URL is not set")
    conn = await asyncpg.connect(database_url)
    try:
        # Fetched up front so a mistyped agency_id reports as a plain
        # "unknown agency" instead of a raw foreign-key traceback.
        feed_url = await conn.fetchval("SELECT feed_url FROM agencies WHERE agency_id = $1", agency_id)
        if feed_url is None:
            raise ValueError(f"no agencies row with agency_id = {agency_id}")
        if probed_url is not None and probed_url != feed_url:
            raise ValueError(
                f"--url probed {probed_url} but agency {agency_id}'s feed_url is {feed_url}; "
                "these feed URLs differ only by an operator number, so check --agency-id "
                "(use --file to record a capture you have vouched for yourself)"
            )
        await record_field_coverage_probe(conn, agency_id, cov, source_feed, ttl_days)
    finally:
        await conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--url", help="Live GTFS-RT trip_updates feed URL to fetch and probe")
    src.add_argument("--file", help="Path to a locally-captured trip_updates protobuf file")
    parser.add_argument("--agency-id", type=int, help="agencies.agency_id this feed belongs to (needed by --record)")
    parser.add_argument(
        "--record",
        action="store_true",
        help="Persist the per-field verdict to rt_field_coverage_probes (needs --agency-id and DATABASE_URL)",
    )
    parser.add_argument(
        "--ttl-days",
        type=int,
        default=DEFAULT_PROBE_TTL_DAYS,
        help=(
            "Days the recorded verdict stays trusted before it must be re-probed "
            f"(default {DEFAULT_PROBE_TTL_DAYS}); 0 records a non-expiring verdict"
        ),
    )
    args = parser.parse_args()
    if args.record and args.agency_id is None:
        parser.error("--record requires --agency-id")
    if args.ttl_days < 0:
        parser.error("--ttl-days must be >= 0 (0 means the verdict does not expire)")

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

    try:
        cov = field_coverage(raw)
    except Exception as e:
        # field_coverage's own top-level _fields(pb_bytes) call already
        # swallows a malformed top-level message, but a per-field _dec()
        # (protobuf-bytes -> str) has no such guard -- an HTML error page or
        # a redirect target masquerading as a feed response can still
        # produce bytes that parse as *some* length-delimited field but
        # aren't valid UTF-8, raising well past that guard. Surface that as
        # a normal "malformed feed" report instead of a raw traceback.
        print(f"DECODE FAILED: response does not decode as GTFS-RT ({e})", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(_assess(cov), indent=2, ensure_ascii=False))

    if not args.record:
        return
    # Report first, record second: an operator who runs --record against an
    # empty overnight capture still sees the coverage report explaining why
    # nothing was written.
    try:
        asyncio.run(
            _record(
                args.agency_id,
                cov,
                args.url or args.file,
                args.ttl_days if args.ttl_days else None,
                probed_url=args.url,
            )
        )
    except ValueError as e:
        print(f"NOT RECORDED: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"recorded probe for agency {args.agency_id}", file=sys.stderr)


if __name__ == "__main__":
    main()
