"""The tracked feed catalogue parses and names each feed once."""

from __future__ import annotations

import csv
import pathlib
from datetime import date

import pytest

from pipeline.feed_probe import Verdict
from scripts.bus_kyo_association_feeds import ALL_FEEDS, ASSOCIATION_SOURCE, CATALOGUE

COLUMNS = [
    "name_ja",
    "prefecture",
    "agency_id",
    "realtime_url",
    "static_url",
    "ingest_strategy",
    "static_strategy",
    "licence",
    "platform",
    "source",
    "verdict",
    "verdict_date",
    "verdict_reason",
    "note",
]
ROOT = pathlib.Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def rows() -> list[dict[str, str]]:
    with CATALOGUE.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        assert reader.fieldnames == COLUMNS
        return list(reader)


def test_catalogue_has_rows_with_the_required_columns_filled(rows):
    assert rows
    for row in rows:
        for column in ("name_ja", "prefecture", "realtime_url", "static_url", "platform", "source"):
            assert row[column].strip(), (row["name_ja"], column)
        assert row["realtime_url"].startswith("https://")
        assert row["static_url"].startswith("https://")


def test_catalogue_has_no_duplicate_feed_urls(rows):
    urls = [row["realtime_url"] for row in rows]
    assert len(urls) == len(set(urls))


def test_a_recorded_verdict_is_a_probe_verdict_with_a_date_and_a_reason(rows):
    for row in rows:
        if not row["verdict"]:
            assert not row["verdict_date"] and not row["verdict_reason"]
            continue
        assert row["verdict"] in {v.value for v in Verdict}
        date.fromisoformat(row["verdict_date"])
        assert row["verdict_reason"].strip()


def test_every_agencies_csv_feed_is_in_the_catalogue(rows):
    with (ROOT / "agencies.csv").open(encoding="utf-8", newline="") as f:
        seeded = {row["feed_url"] for row in csv.DictReader(f)}
    assert seeded <= {row["realtime_url"] for row in rows}


def test_bus_kyo_script_reads_the_catalogue_rows_of_the_association(rows):
    association = [row["realtime_url"] for row in rows if row["source"] == ASSOCIATION_SOURCE]
    assert [f.realtime_url for f in ALL_FEEDS] == association
