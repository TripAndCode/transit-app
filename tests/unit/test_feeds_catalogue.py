"""The tracked feed catalogue parses, names each feed once, and agrees with
agencies.csv and with the deploy guides."""

from __future__ import annotations

import csv
import importlib.util
import pathlib
import re
from datetime import date

import pytest

from pipeline.feed_probe import DEFAULT_PROBE_SECONDS, JOIN_RATE_MIN, Verdict
from pipeline.poller import DEFAULT_INTERVAL_SEC
from pipeline.strategies.static_join import RT_COVERAGE_FIELDS
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


def test_an_agency_id_goes_with_a_strategy(rows):
    for row in rows:
        assert bool(row["agency_id"]) == bool(row["ingest_strategy"]), row["name_ja"]


def test_coverage_refuted_names_probe_fields_and_needs_a_verdict(rows):
    for row in rows:
        tokens = row["coverage_refuted"].split()
        assert set(tokens) <= set(RT_COVERAGE_FIELDS), f"{row['name_ja']}: {tokens}"
        if not row["verdict"]:
            assert not tokens, row["name_ja"]


def test_no_agencies_csv_row_sets_a_trip_id_pattern_the_catalogue_cannot_carry():
    with (ROOT / "agencies.csv").open(encoding="utf-8", newline="") as f:
        assert [row["agency_name"] for row in csv.DictReader(f) if row["trip_id_pattern"]] == []


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


def _first_cells(text: str, header: str) -> set[str]:
    """The backticked names in the first cell of each row of the table whose
    header row starts with `header`."""
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(f"| {header} |"))
    names: set[str] = set()
    for line in lines[start + 2 :]:
        if not line.startswith("|"):
            break
        names.update(re.findall(r"`([^`]+)`", line.split("|")[1]))
    return names


def _mentions(number: int, text: str) -> bool:
    return re.search(rf"(?<!\d){number}(?!\d)", text) is not None


@pytest.mark.parametrize(("guide", "header"), [(GUIDES[0], "判定"), (GUIDES[1], "Verdict")])
def test_a_guides_verdict_table_lists_exactly_the_probe_verdicts(guide, header):
    text = (ROOT / guide).read_text(encoding="utf-8")
    assert _first_cells(text, header) == {v.value for v in Verdict}


@pytest.mark.parametrize("guide", GUIDES)
def test_a_guide_states_the_probe_and_poll_defaults(guide):
    text = (ROOT / guide).read_text(encoding="utf-8")
    assert f"{JOIN_RATE_MIN:.0%}" in text
    assert _mentions(int(DEFAULT_PROBE_SECONDS), text)
    (poll_row,) = [line for line in text.splitlines() if line.startswith("| `POLL_INTERVAL_SEC`")]
    assert _mentions(int(DEFAULT_INTERVAL_SEC), poll_row)
