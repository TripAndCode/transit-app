"""Bulk-onboarding step for the Hiroshima Bus Association's published GTFS
feed list.

The association (公益社団法人広島県バス協会, https://www.bus-kyo.or.jp/gtfs-open-data)
lists 17 operators. The list lives in the tracked catalogue ``feeds/japan.csv``
(rows whose ``source`` is ``bus-kyo.or.jp``), and ``pending_feeds()`` computes
which of them are both supported (see ``BusKyoFeed.supported``) and not yet
present in ``agencies.csv`` (matched by ``feed_url``), so a future
association update only needs a catalogue edit rather than hand-typing CSV
rows again.

Most of these operators share one ``mcapps.jp`` platform (same URL shape,
same protobuf producer) and can reuse the existing
``static_join``/``direct_url`` strategies unchanged. The ``busit.jp`` and
``bus-vision.jp`` rows name no strategy in the catalogue, so this script
leaves them out of ``agencies.csv``; ``gtfs_pipeline.py add_agency --feed-url
... --static-url ...`` probes such a feed and chooses the strategies itself,
and the catalogue records its last verdict.

Even for an ``mcapps.jp`` operator, sharing a platform is not the same as
sharing confirmed optional-field coverage (see
``pipeline/strategies/static_join.py: field_coverage``). The numbers that
depend on those fields (``service_delivered``'s executed share, ``dwell_run``
and the other gated readers) stay unavailable for an agency until a probe
verdict is recorded for it. After ``seed_agencies`` has loaded the new
rows, run ``scripts/probe_rt_field_coverage.py --url <realtime_url>
--agency-id <id> --record`` against each new operator's live feed during
service hours.
Without ``--agency-id``/``--record`` the probe only prints a report.

Usage:
    poetry run python scripts/bus_kyo_association_feeds.py --check
    poetry run python scripts/bus_kyo_association_feeds.py --write --agencies-csv agencies.csv
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]


CATALOGUE = ROOT / "feeds" / "japan.csv"
ASSOCIATION_SOURCE = "bus-kyo.or.jp"


@dataclasses.dataclass(frozen=True)
class BusKyoFeed:
    agency_id: int | None
    name_ja: str
    platform: str
    static_url: str
    realtime_url: str | None
    ingest_strategy: str | None
    static_strategy: str | None
    note: str = ""

    @property
    def supported(self) -> bool:
        """True when this row is ready to seed into agencies.csv as-is.

        False for a feed whose catalogue row names no ingest strategy: the
        platform's fetch/wire shape is not confirmed against
        pipeline/strategies/, so reusing static_join/direct_url would be a guess.
        """
        return self.ingest_strategy is not None and self.realtime_url is not None


def load_association_feeds(path: pathlib.Path = CATALOGUE) -> tuple[BusKyoFeed, ...]:
    """The association's rows of the feed catalogue, in file order."""
    with path.open(encoding="utf-8", newline="") as f:
        rows = [row for row in csv.DictReader(f) if row["source"] == ASSOCIATION_SOURCE]
    return tuple(
        BusKyoFeed(
            int(row["agency_id"]) if row["agency_id"] else None,
            row["name_ja"],
            row["platform"],
            row["static_url"],
            row["realtime_url"] or None,
            row["ingest_strategy"] or None,
            row["static_strategy"] or None,
            row["note"],
        )
        for row in rows
    )


ALL_FEEDS: tuple[BusKyoFeed, ...] = load_association_feeds()


def pending_feeds(existing_feed_urls: set[str]) -> list[BusKyoFeed]:
    """Feeds ready to seed that aren't already in agencies.csv.

    Excludes both already-configured feeds (matched by realtime_url against
    feed_url, which the agencies table holds UNIQUE) and unsupported-platform
    feeds (see BusKyoFeed.supported).
    """
    return [f for f in ALL_FEEDS if f.supported and f.realtime_url not in existing_feed_urls]


def _read_existing_feed_urls(agencies_csv_path: pathlib.Path) -> set[str]:
    if not agencies_csv_path.exists():
        return set()
    with agencies_csv_path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return {row["feed_url"].strip() for row in reader if row.get("feed_url")}


# Matches agencies.csv's own header exactly -- written once, only when
# --write's target file doesn't already exist, so a subsequent --write run
# against the same file never lets csv.DictReader (in
# _read_existing_feed_urls) treat a data row as the header.
_CSV_HEADER = [
    "agency_id",
    "agency_name",
    "feed_url",
    "static_url",
    "ingest_strategy",
    "static_strategy",
    "trip_id_pattern",
]


def _to_csv_row(feed: BusKyoFeed) -> list[str]:
    return [
        str(feed.agency_id) if feed.agency_id is not None else "",
        feed.name_ja,
        feed.realtime_url or "",
        feed.static_url,
        feed.ingest_strategy or "",
        feed.static_strategy or "",
        "",  # trip_id_pattern: unused by static_join (opaque UUID trip_id)
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--agencies-csv", default=str(ROOT / "agencies.csv"), help="Path to agencies.csv (default: repo root)"
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="Print what would be added, without writing")
    mode.add_argument("--write", action="store_true", help="Append new rows to --agencies-csv")
    args = parser.parse_args()

    csv_path = pathlib.Path(args.agencies_csv)
    existing = _read_existing_feed_urls(csv_path)
    pending = pending_feeds(existing)
    unsupported = [f for f in ALL_FEEDS if not f.supported and f.realtime_url not in existing]

    if args.check or not args.write:
        print(f"{len(pending)} new operator(s) ready to add to {csv_path}:")
        for f in pending:
            print(f"  {f.agency_id}: {f.name_ja} ({f.platform})")
        if unsupported:
            print(f"\n{len(unsupported)} operator(s) not yet onboardable:")
            for f in unsupported:
                print(f"  {f.name_ja} ({f.platform}): {f.note}")

    if args.write:
        if not pending:
            print("Nothing to add.")
            return
        # Written only when the target file doesn't already exist, so a
        # second --write run against the same file appends under the header
        # this run just wrote -- never a headerless file that would make a
        # later _read_existing_feed_urls() call (via csv.DictReader) consume
        # the first data row as field names.
        write_header = not csv_path.exists()
        with csv_path.open("a", encoding="utf-8", newline="") as csv_file:
            writer = csv.writer(csv_file)
            if write_header:
                writer.writerow(_CSV_HEADER)
            for feed in pending:
                writer.writerow(_to_csv_row(feed))
        print(f"Appended {len(pending)} row(s) to {csv_path}.")
        print(
            "Next: after seed_agencies, run "
            "scripts/probe_rt_field_coverage.py --url <realtime_url> --agency-id <id> --record "
            "against each newly-added operator's feed during service hours; its "
            "coverage-gated numbers (service_delivered, dwell_run) stay unavailable until a "
            "verdict is recorded."
        )


if __name__ == "__main__":
    main()
