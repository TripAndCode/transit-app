"""ja/en parity of the server-side template tables keyed by ``(key, lang)``.

In most of them a key present in one locale only silently serves the other
(the lookup falls back to ja); the report-definition tables index directly
and raise only when a report in that locale renders. A placeholder that
exists in one locale's template but not the other's is swallowed by the
formatting helpers' ``KeyError`` guards, or raises where a helper has none, so
no failure surfaces before the affected text is rendered.
"""

import string

import pytest

from pipeline.digest import render as digest_render
from pipeline.query import chat, formatter, tools
from pipeline.reports import definition, forecast

_TABLES = {
    "tools._LOCALES": tools._LOCALES,
    "formatter._LOCALES": formatter._LOCALES,
    "chat._CHAT_STRINGS": chat._CHAT_STRINGS,
    "digest.render._DIGEST_LOCALES": digest_render._DIGEST_LOCALES,
    "forecast._DISCLAIMER": forecast._DISCLAIMER,
    "definition._MEASUREMENT_POINT_TEXT": definition._MEASUREMENT_POINT_TEXT,
    "definition._DEDUP_RULE_TEXT": definition._DEDUP_RULE_TEXT,
}


def _placeholders(template: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(template) if name is not None}


@pytest.mark.parametrize("table", _TABLES)
def test_every_key_has_both_locales(table):
    entries = _TABLES[table]
    keys = {key for key, _lang in entries}
    missing = sorted(f"{key}/{lang}" for key in keys for lang in ("ja", "en") if (key, lang) not in entries)
    assert not missing, f"{table} lacks a locale for: {missing}"


@pytest.mark.parametrize("table", _TABLES)
def test_only_ja_and_en_are_used(table):
    assert {lang for _key, lang in _TABLES[table]} == {"ja", "en"}


@pytest.mark.parametrize("table", _TABLES)
def test_both_locales_use_the_same_placeholders(table):
    entries = _TABLES[table]
    mismatched = {
        key: (sorted(_placeholders(entries[(key, "ja")])), sorted(_placeholders(entries[(key, "en")])))
        for key, lang in entries
        if lang == "ja"
        and (key, "en") in entries
        and _placeholders(entries[(key, "ja")]) != _placeholders(entries[(key, "en")])
    }
    assert not mismatched, f"{table} placeholder sets differ between ja and en: {mismatched}"


def test_the_placeholder_extractor_catches_a_typo_and_ignores_escaped_braces():
    assert _placeholders("{route}: {count} 件") == {"route", "count"}
    assert _placeholders("{route}") != _placeholders("{rout}")
    assert _placeholders("{{literal}} {n:.1f}") == {"n"}
