"""Pure-logic tests for the Hiroshima Bus Association bulk-onboarding step.

No DB, no network -- these only exercise the data table + CSV-row generation
in scripts/bus_kyo_association_feeds.py.
"""

from __future__ import annotations

import csv

from scripts.bus_kyo_association_feeds import (
    ALL_FEEDS,
    BusKyoFeed,
    _read_existing_feed_urls,
    _to_csv_row,
    main,
    pending_feeds,
)

_HEADER = [
    "agency_id",
    "agency_name",
    "feed_url",
    "static_url",
    "ingest_strategy",
    "static_strategy",
    "trip_id_pattern",
]


def _write_agencies_csv(path, rows):
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(_HEADER)
        for row in rows:
            writer.writerow(row)


def test_all_feeds_have_unique_realtime_urls_where_present():
    urls = [f.realtime_url for f in ALL_FEEDS if f.realtime_url]
    assert len(urls) == len(set(urls))


def test_supported_requires_ingest_strategy_and_realtime_url():
    supported = BusKyoFeed(1, "テスト", "mcapps.jp", "http://x/static.zip", "http://x/rt.bin", "static_join", "direct_url")
    assert supported.supported

    no_strategy = BusKyoFeed(None, "テスト2", "bus-vision.jp", "http://y/static.zip", None, None, None)
    assert not no_strategy.supported

    no_url_known = BusKyoFeed(None, "テスト3", "bus-vision.jp", "http://z/static.zip", None, "static_join", "direct_url")
    assert not no_url_known.supported


def test_pending_feeds_skips_already_configured_agencies():
    already = {f.realtime_url for f in ALL_FEEDS if f.agency_id in (8, 9, 10)}
    pending = pending_feeds(already)
    assert {f.agency_id for f in pending}.isdisjoint({8, 9, 10})
    assert len(pending) > 0


def test_pending_feeds_excludes_unsupported_platforms_even_when_absent():
    pending = pending_feeds(existing_feed_urls=set())
    for f in pending:
        assert f.platform == "mcapps.jp"
        assert f.ingest_strategy == "static_join"
        assert f.static_strategy == "direct_url"


def test_pending_feeds_empty_when_all_realtime_urls_already_present():
    all_urls = {f.realtime_url for f in ALL_FEEDS if f.realtime_url}
    assert pending_feeds(all_urls) == []


def test_to_csv_row_matches_agencies_csv_column_order():
    feed = ALL_FEEDS[3]  # agency_id 11, 芸陽バス
    row = _to_csv_row(feed)
    assert row == [
        str(feed.agency_id),
        feed.name_ja,
        feed.realtime_url,
        feed.static_url,
        "static_join",
        "direct_url",
        "",
    ]
    assert len(row) == len(_HEADER)


def test_read_existing_feed_urls(tmp_path):
    csv_path = tmp_path / "agencies.csv"
    hiroden_url = "https://ajt-mobusta-gtfs.mcapps.jp/realtime/8/trip_updates.bin"
    _write_agencies_csv(
        csv_path,
        [["8", "広島電鉄", hiroden_url, "", "static_join", "direct_url", ""]],
    )
    urls = _read_existing_feed_urls(csv_path)
    assert urls == {hiroden_url}


def test_read_existing_feed_urls_missing_file_is_empty(tmp_path):
    assert _read_existing_feed_urls(tmp_path / "nope.csv") == set()


def test_write_appends_only_pending_rows_and_is_idempotent(tmp_path, monkeypatch, capsys):
    csv_path = tmp_path / "agencies.csv"
    base = "https://ajt-mobusta-gtfs.mcapps.jp/realtime"
    _write_agencies_csv(
        csv_path,
        [
            ["8", "広島電鉄", f"{base}/8/trip_updates.bin", "", "static_join", "direct_url", ""],
            ["9", "広島バス", f"{base}/9/trip_updates.bin", "", "static_join", "direct_url", ""],
            ["10", "広島交通", f"{base}/10/trip_updates.bin", "", "static_join", "direct_url", ""],
        ],
    )
    before_line_count = len(csv_path.read_text(encoding="utf-8").splitlines())

    monkeypatch.setattr("sys.argv", ["prog", "--agencies-csv", str(csv_path), "--write"])
    main()

    lines = csv_path.read_text(encoding="utf-8").splitlines()
    supported_pending = [f for f in ALL_FEEDS if f.supported and f.agency_id not in (8, 9, 10)]
    assert len(lines) == before_line_count + len(supported_pending)

    # Re-running --write with the now-updated file must add nothing further.
    monkeypatch.setattr("sys.argv", ["prog", "--agencies-csv", str(csv_path), "--write"])
    main()
    out = capsys.readouterr().out
    assert "Nothing to add." in out
    assert len(csv_path.read_text(encoding="utf-8").splitlines()) == len(lines)


def test_write_creates_header_when_target_file_does_not_exist_and_stays_idempotent(tmp_path, monkeypatch, capsys):
    """A first --write against a path with no existing agencies.csv must
    write the header row before appending -- otherwise a second --write run
    would open the headerless file with csv.DictReader (via
    _read_existing_feed_urls), consume the first data row as field names,
    and silently duplicate every entry instead of adding nothing."""
    csv_path = tmp_path / "agencies.csv"
    assert not csv_path.exists()

    monkeypatch.setattr("sys.argv", ["prog", "--agencies-csv", str(csv_path), "--write"])
    main()

    lines = csv_path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == ",".join(_HEADER)
    supported = [f for f in ALL_FEEDS if f.supported]
    assert len(lines) == 1 + len(supported)

    # Re-running --write against the now-populated file must add nothing
    # further -- this is exactly the idempotency the header write protects.
    monkeypatch.setattr("sys.argv", ["prog", "--agencies-csv", str(csv_path), "--write"])
    main()
    out = capsys.readouterr().out
    assert "Nothing to add." in out
    assert csv_path.read_text(encoding="utf-8").splitlines() == lines
