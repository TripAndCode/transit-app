"""The tracked feed catalogue parses, names each feed once, and agrees with
agencies.csv and with the deploy guides."""

from __future__ import annotations

import csv
import importlib.util
import pathlib
from datetime import date

import pytest

from pipeline.feed_probe import DEFAULT_PROBE_SECONDS, JOIN_RATE_MIN, Verdict
from scripts.bus_kyo_association_feeds import ALL_FEEDS, CATALOGUE

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
    "probe_seconds",
    "verdict_reason",
    "coverage_refuted",
    "note",
]
ROOT = pathlib.Path(__file__).resolve().parents[2]
GUIDES = ("docs/deploy-standalone.md", "docs/deploy-standalone.en.md")


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
            assert row[column].strip(), f"{row['name_ja']}: {column} is empty"
        assert row["realtime_url"].startswith("https://"), row["name_ja"]
        assert row["static_url"].startswith("https://"), row["name_ja"]


def test_catalogue_has_no_duplicate_feed_urls(rows):
    urls = [row["realtime_url"] for row in rows]
    assert len(urls) == len(set(urls))


def test_agency_id_is_blank_or_digits_and_not_repeated(rows):
    ids = []
    for row in rows:
        assert row["agency_id"] == "" or row["agency_id"].isdigit(), f"{row['name_ja']}: agency_id {row['agency_id']!r}"
        if row["agency_id"]:
            ids.append(row["agency_id"])
    assert len(ids) == len(set(ids))


def test_strategies_are_both_set_or_both_blank_and_name_a_module(rows):
    for row in rows:
        ingest, static = row["ingest_strategy"], row["static_strategy"]
        assert bool(ingest) == bool(static), f"{row['name_ja']}: only one strategy is set"
        for name in (ingest, static):
            if name:
                assert importlib.util.find_spec(f"pipeline.strategies.{name}"), f"{row['name_ja']}: no strategy {name}"


def test_a_recorded_verdict_is_a_probe_verdict_with_a_date_a_length_and_a_reason(rows):
    for row in rows:
        who = row["name_ja"]
        if not row["verdict"]:
            assert not (row["verdict_date"] or row["probe_seconds"] or row["verdict_reason"]), who
            continue
        assert row["verdict"] in {v.value for v in Verdict}, who
        date.fromisoformat(row["verdict_date"])
        assert float(row["probe_seconds"]) > 0, who
        assert row["verdict_reason"].strip(), who


def test_agencies_csv_is_a_checked_copy_of_the_catalogue(rows):
    with (ROOT / "agencies.csv").open(encoding="utf-8", newline="") as f:
        seeded = {row["agency_id"]: row for row in csv.DictReader(f)}
    with_id = [row for row in rows if row["agency_id"]]
    assert {row["agency_id"] for row in with_id} == set(seeded)
    for row in with_id:
        copy = seeded[row["agency_id"]]
        who = f"{row['name_ja']} ({row['agency_id']})"
        assert copy["agency_name"] == row["name_ja"], who
        assert copy["feed_url"] == row["realtime_url"], who
        assert copy["static_url"] == row["static_url"], who
        assert copy["ingest_strategy"] == row["ingest_strategy"], who
        assert copy["static_strategy"] == row["static_strategy"], who


def test_bus_kyo_script_reads_the_catalogue_rows_of_the_association(rows):
    association = [row["realtime_url"] for row in rows if row["source"] == "bus-kyo.or.jp"]
    assert association
    assert [f.realtime_url for f in ALL_FEEDS] == association


@pytest.mark.parametrize("guide", GUIDES)
def test_a_guide_states_every_verdict_and_the_probe_thresholds(guide):
    text = (ROOT / guide).read_text(encoding="utf-8")
    for verdict in Verdict:
        assert f"`{verdict.value}`" in text, f"{guide} omits {verdict.value}"
    assert f"{int(JOIN_RATE_MIN * 100)}%" in text
    assert f"{int(DEFAULT_PROBE_SECONDS)}" in text
