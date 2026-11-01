"""The open-data credit a CSV export carries for an agency's feed.

The expected lines live in ``tests/fixtures/data_source_credit_lines.json``,
which ``frontend/src/components/dataSources.test.ts`` pins the browser's
renderer to as well, so a CSV cell and a printed page credit a source in the
same words.
"""

import csv
import json
from pathlib import Path

import pytest

from pipeline.data_sources import credit_line, source_of

ROOT = Path(__file__).resolve().parents[2]
EXPECTED: dict[str, dict[str, str]] = json.loads(
    (ROOT / "tests" / "fixtures" / "data_source_credit_lines.json").read_text(encoding="utf-8")
)
SOURCES: list[dict] = json.loads(
    (ROOT / "frontend" / "src" / "components" / "dataSources.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize("locale", ["ja", "en"])
@pytest.mark.parametrize("feed_url", sorted(EXPECTED))
def test_credit_line_follows_each_linked_name_with_its_url(feed_url, locale):
    assert credit_line(feed_url, locale) == EXPECTED[feed_url][locale]


def _parenthesis_depth(text: str) -> int:
    depth = deepest = 0
    for char in text:
        if char in "（(":
            depth += 1
            deepest = max(deepest, depth)
        elif char in "）)":
            depth -= 1
    return deepest


@pytest.mark.parametrize("locale", ["ja", "en"])
@pytest.mark.parametrize("source", SOURCES, ids=lambda source: source["creditKey"])
def test_no_credit_nests_a_url_inside_parentheses(source, locale):
    feed = source["feeds"][0]
    line = credit_line(f"https://{feed['host']}{feed.get('pathPrefix', '/')}", locale)
    assert line is not None
    assert _parenthesis_depth(line) == 1, line


def test_aomori_credit_names_provider_material_licence_and_modification():
    assert credit_line("https://aomoricitybus.com/TripUpdate.pb", "ja") == (
        "本サービスは、以下の著作物を改変して利用しています。青森市企業局交通部、"
        "青森市営バス路線情報（https://ckan.odpt.org/dataset/aomori_city_all_lines）および"
        "【リアルタイム情報】青森市営バス路線情報（https://ckan.odpt.org/dataset/odpt_aomori_city_all_lines）、"
        "クリエイティブ・コモンズ・ライセンス 表示4.0国際（https://creativecommons.org/licenses/by/4.0/deed.ja）"
    )


def test_an_unsupported_locale_reads_the_japanese_credit():
    url = "https://ajt-mobusta-gtfs.mcapps.jp/realtime/8/trip_updates.bin"
    assert credit_line(url, "fr") == EXPECTED[url]["ja"]


@pytest.mark.parametrize("feed_url", [None, "", "not a url", "http://example.com/feed.pb"])
def test_no_credit_for_a_feed_no_source_covers(feed_url):
    assert credit_line(feed_url, "ja") is None


def test_odpt_mirror_credits_only_the_aomori_dataset_path():
    mirror = "https://api-public.odpt.org/api/v4/gtfs/realtime/"
    assert source_of(mirror + "odpt_SomeOtherCity_AllLines_trip_update") is None
    assert source_of(mirror + "odpt_AomoriCity_AllLines_trip_update") is source_of(
        "https://aomoricitybus.com/TripUpdate.pb"
    )


def test_every_seeded_agency_feed_has_a_credit():
    with (ROOT / "agencies.csv").open(encoding="utf-8", newline="") as f:
        urls = [url for row in csv.DictReader(f) for url in (row["feed_url"], row["static_url"]) if url]
    assert urls
    uncovered = [url for url in urls if credit_line(url, "ja") is None]
    assert not uncovered, f"no credited data source covers: {uncovered}"
