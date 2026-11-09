"""The methodology documents stay in step with the method they describe.

Each document's changelog is generated from ``METHOD_VERSIONS``
(``scripts/render_method_changelog.py``), so a new method version fails here
until both documents carry it. The two languages keep the same sections, and
the links every export and the app footer print lead to them.
"""

from pathlib import Path

import pytest

from pipeline.agg_registry import METHOD_VERSION, METHOD_VERSIONS, MethodVersion
from pipeline.reports.definition import METHODOLOGY_URLS
from scripts import render_method_changelog as changelog

ROOT = Path(__file__).resolve().parents[2]
REPO_BLOB = "https://github.com/TripAndCode/transit-app/blob/main/"


@pytest.mark.parametrize("locale", sorted(changelog.DOCS))
def test_each_document_carries_the_changelog_method_versions_makes(locale):
    path = changelog.DOCS[locale]
    block = changelog.section(path.read_text(encoding="utf-8"))
    assert block == changelog.render(locale), (
        f"{path.relative_to(ROOT)}'s method changelog is not what METHOD_VERSIONS makes: run `{changelog.COMMAND}`"
    )


@pytest.mark.parametrize("locale", sorted(changelog.DOCS))
def test_the_changelog_names_the_current_method_first(locale):
    rows = [line for line in changelog.render(locale).splitlines() if line.startswith("| `")]
    assert [row.split("`")[1] for row in rows] == [entry.version for entry in reversed(METHOD_VERSIONS)]
    assert f"`{METHOD_VERSION}`" in changelog.render(locale).split("\n|", 1)[0]


def test_a_new_method_version_leaves_the_documents_stale():
    later = (*METHOD_VERSIONS, MethodVersion("2099.1.0", {"agg_route_daily": 99, "agg_stop_routes": None}))
    block = changelog.render("en", later)
    assert block != changelog.render("en")
    assert "| `2099.1.0` | `agg_route_daily` " in block
    assert "`agg_stop_routes` " in block and " → retired" in block


def test_rewriting_replaces_only_the_block():
    text = f"# Title\n\nbefore\n\n{changelog.START}\nold\n{changelog.END}\n\nafter\n"
    assert changelog.rewritten(text, "ja") == f"# Title\n\nbefore\n\n{changelog.render('ja')}\n\nafter\n"
    with pytest.raises(ValueError):
        changelog.rewritten("# no block\n", "ja")


def _headings(path: Path) -> list[str]:
    return [line.split(" ", 1)[0] for line in path.read_text(encoding="utf-8").splitlines() if line.startswith("#")]


def test_both_languages_have_the_same_outline():
    assert _headings(changelog.DOCS["ja"]) == _headings(changelog.DOCS["en"]), (
        "docs/methodology.md and docs/methodology.en.md must keep the same sections in the same order"
    )


def test_the_links_lead_to_the_documents():
    assert sorted(METHODOLOGY_URLS) == sorted(changelog.DOCS)
    for locale, url in METHODOLOGY_URLS.items():
        assert url.startswith(REPO_BLOB), url
        assert ROOT / url.removeprefix(REPO_BLOB) == changelog.DOCS[locale]
