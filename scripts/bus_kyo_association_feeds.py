"""Bulk-onboarding step for the Hiroshima Bus Association's published GTFS
feed list.

The association (公益社団法人広島県バス協会, https://www.bus-kyo.or.jp/gtfs-open-data)
lists 17 operators. This module holds that full list as data (fetched by a
human session with network access -- an autonomous session here has none,
see ``docs/refactor-log.md``) and ``pending_feeds()`` dynamically computes
which of them are both supported (see ``BusKyoFeed.supported``) and not yet
present in ``agencies.csv`` (matched by ``feed_url``), so a future
association update only needs a data-table edit here rather than hand-typing
CSV rows again.

Most of these operators share the same ``mcapps.jp`` platform as 8/9/10
(same URL shape, same protobuf producer) and can reuse the existing
``static_join``/``direct_url`` strategies unchanged. A few use a different
platform (``busit.jp``, ``bus-vision.jp``) whose fetch/wire shape hasn't been
confirmed against ``pipeline/strategies/`` -- those are recorded for
visibility but marked unsupported (``ingest_strategy=None``) rather than
guessed at, per this repo's rule against assuming an unconfirmed strategy
shape.

Even for an ``mcapps.jp`` operator, sharing a platform is not the same as
sharing confirmed optional-field coverage (see
``pipeline/strategies/static_join.py: field_coverage`` and
``scripts/probe_rt_field_coverage.py``) -- run the probe against each new
feed's live realtime_url before treating its ``service_delivered``/
``dwell_run`` numbers as trustworthy, the same way that check was applied to
8/9/10 before their optional-field-dependent report columns were trusted.

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

        False for a platform whose fetch/wire shape isn't confirmed against
        pipeline/strategies/ yet (needs a new strategy module first, not a
        guess at reusing static_join/direct_url for a different shape).
        """
        return self.ingest_strategy is not None and self.realtime_url is not None


# Already configured in agencies.csv -- listed here too so `pending_feeds`
# can skip them by feed_url, the same dedup key gtfs_pipeline.py's
# seed_agencies uses via its ON CONFLICT(feed_url) semantics.
_MCAPPS_BASE = "https://ajt-mobusta-gtfs.mcapps.jp"

ALL_FEEDS: tuple[BusKyoFeed, ...] = (
    BusKyoFeed(8, "広島電鉄", "mcapps.jp", f"{_MCAPPS_BASE}/static/8/current_data.zip",
               f"{_MCAPPS_BASE}/realtime/8/trip_updates.bin", "static_join", "direct_url"),
    BusKyoFeed(9, "広島バス", "mcapps.jp", f"{_MCAPPS_BASE}/static/9/current_data.zip",
               f"{_MCAPPS_BASE}/realtime/9/trip_updates.bin", "static_join", "direct_url"),
    BusKyoFeed(10, "広島交通", "mcapps.jp", f"{_MCAPPS_BASE}/static/10/current_data.zip",
               f"{_MCAPPS_BASE}/realtime/10/trip_updates.bin", "static_join", "direct_url"),
    BusKyoFeed(11, "芸陽バス", "mcapps.jp", f"{_MCAPPS_BASE}/static/11/current_data.zip",
               f"{_MCAPPS_BASE}/realtime/11/trip_updates.bin", "static_join", "direct_url"),
    BusKyoFeed(12, "備北交通", "mcapps.jp", f"{_MCAPPS_BASE}/static/12/current_data.zip",
               f"{_MCAPPS_BASE}/realtime/12/trip_updates.bin", "static_join", "direct_url"),
    BusKyoFeed(13, "エイチ・ディー西広島", "mcapps.jp", f"{_MCAPPS_BASE}/static/13/current_data.zip",
               f"{_MCAPPS_BASE}/realtime/13/trip_updates.bin", "static_join", "direct_url"),
    BusKyoFeed(14, "フォーブル", "mcapps.jp", f"{_MCAPPS_BASE}/static/14/current_data.zip",
               f"{_MCAPPS_BASE}/realtime/14/trip_updates.bin", "static_join", "direct_url"),
    BusKyoFeed(15, "JRバス中国", "mcapps.jp", f"{_MCAPPS_BASE}/static/15/current_data.zip",
               f"{_MCAPPS_BASE}/realtime/15/trip_updates.bin", "static_join", "direct_url"),
    BusKyoFeed(17, "ささき観光", "mcapps.jp", f"{_MCAPPS_BASE}/static/17/current_data.zip",
               f"{_MCAPPS_BASE}/realtime/17/trip_updates.bin", "static_join", "direct_url"),
    BusKyoFeed(18, "呉市生活バス", "mcapps.jp", f"{_MCAPPS_BASE}/static/18/current_data.zip",
               f"{_MCAPPS_BASE}/realtime/18/trip_updates.bin", "static_join", "direct_url"),
    BusKyoFeed(19, "廿日市市自主運行バス", "mcapps.jp", f"{_MCAPPS_BASE}/static/19/current_data.zip",
               f"{_MCAPPS_BASE}/realtime/19/trip_updates.bin", "static_join", "direct_url"),
    BusKyoFeed(53, "おのみちバス", "mcapps.jp", f"{_MCAPPS_BASE}/static/53/current_data.zip",
               f"{_MCAPPS_BASE}/realtime/53/trip_updates.bin", "static_join", "direct_url"),
    BusKyoFeed(54, "朝日交通", "mcapps.jp", f"{_MCAPPS_BASE}/static/54/current_data.zip",
               f"{_MCAPPS_BASE}/realtime/54/trip_updates.bin", "static_join", "direct_url"),
    # Different platform (busit.jp): fetch/wire shape not confirmed against
    # pipeline/strategies/ -- direct_url.py's manifest/latest.zip-preference
    # logic and static_join.py's URL assumptions are both mcapps.jp-shaped.
    # etajimabus's static endpoint additionally has no separate "future"
    # variant (only "current"), unlike the mcapps.jp operators.
    BusKyoFeed(None, "江田島バス", "busit.jp", "https://gtfs-st.busit.jp/api/etajimabus",
               "https://gtfs-rt.busit.jp/api/Etajimabus/TripUpdates", None, None,
               note="needs a new ingest/static strategy for busit.jp's shape before onboarding"),
    # Different platform (bus-vision.jp): the association's page only gives a
    # realtime endpoint *family* (TripUpdate/VehiclePosition/ServiceAlert),
    # not one fixed trip_updates binary path -- the exact URL to fetch isn't
    # known yet, on top of the same needs-a-new-strategy gap as busit.jp.
    BusKyoFeed(None, "株式会社中国バス", "bus-vision.jp", "https://bus-vision.jp/gtfs_v2/chugokubus/gtfsFeed",
               None, None, None,
               note="needs a new strategy for bus-vision.jp's shape, and the exact "
                    "TripUpdate realtime URL (only a family pattern is known)"),
    BusKyoFeed(None, "鞆鉄道", "bus-vision.jp", "https://bus-vision.jp/gtfs_v2/tomotetsubus/gtfsFeed",
               None, None, None,
               note="needs a new strategy for bus-vision.jp's shape, and the exact "
                    "TripUpdate realtime URL (only a family pattern is known)"),
    BusKyoFeed(None, "株式会社井笠バスカンパニー", "bus-vision.jp", "https://bus-vision.jp/gtfs_v2/ikasabus/gtfsFeed",
               None, None, None,
               note="needs a new strategy for bus-vision.jp's shape, and the exact "
                    "TripUpdate realtime URL (only a family pattern is known)"),
)


def pending_feeds(existing_feed_urls: set[str]) -> list[BusKyoFeed]:
    """Feeds ready to seed that aren't already in agencies.csv.

    Excludes both already-configured feeds (matched by realtime_url, the
    same dedup key gtfs_pipeline.py's seed_agencies uses via feed_url's
    UNIQUE constraint) and unsupported-platform feeds (see
    BusKyoFeed.supported).
    """
    return [
        f for f in ALL_FEEDS
        if f.supported and f.realtime_url not in existing_feed_urls
    ]


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
        print("Next: run scripts/probe_rt_field_coverage.py --url <realtime_url> against each "
              "newly-added operator's feed before trusting its service_delivered/dwell_run numbers.")


if __name__ == "__main__":
    main()
