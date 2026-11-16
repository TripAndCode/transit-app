"""The server-side reader-facing text names the counted unit one way.

The server counterpart of the glossary guard in
``tests/frontend/i18n_glossary.test.mjs``: the same retired names, checked in
the template tables a reader sees through Ask, the council text, the digest and
the forecast, weather and performance-standard disclaimers. The unit is an
observation (観測数); the narrower patterns below name only what was retired, so
サンプルデータ, "measurement point" and an unrelated "sample" still pass.
"""

import re

import pytest

from pipeline.digest import render as digest_render
from pipeline.query import formatter, meta_tools, tools
from pipeline.reports import forecast, performance_standard, weather

_JA_RETIRED = [
    re.compile(r"観測データ\s*(数|件)"),
    re.compile(r"サンプル数|少数サンプル|サンプルが少な"),
    re.compile(r"計測(回数|記録|が少な|の平均|から)|これまでの計測"),
]
_EN_RETIRED = [
    re.compile(r"\bsamples\b", re.IGNORECASE),
    re.compile(r"\bmeasurements\b", re.IGNORECASE),
    re.compile(r"\blow sample\b", re.IGNORECASE),
    re.compile(r"\bsample counts?\b", re.IGNORECASE),
]

# A `{samples}` placeholder is a field name, not the word a reader sees; a space
# keeps the words around it apart (観測データ{samples}件 reads 観測データ 件).
_PLACEHOLDER = re.compile(r"\{[^}]*\}")


def _by_locale_key(table):
    return [(lang, text) for (_key, lang), text in table.items()]


_VALUES = {
    "tools._LOCALES": _by_locale_key(tools._LOCALES),
    "formatter._LOCALES": _by_locale_key(formatter._LOCALES),
    "digest.render._DIGEST_LOCALES": _by_locale_key(digest_render._DIGEST_LOCALES),
    "forecast._DISCLAIMER": _by_locale_key(forecast._DISCLAIMER),
    "weather._DISCLAIMER": list(weather._DISCLAIMER.items()),
    "performance_standard._DISCLAIMER": list(performance_standard._DISCLAIMER.items()),
    "meta_tools._CAPABILITY_EXAMPLES": [("ja", t) for t in meta_tools._CAPABILITY_EXAMPLES_JP.values()]
    + [("en", t) for t in meta_tools._CAPABILITY_EXAMPLES_EN.values()],
    "meta_tools._METRIC_LABELS": [
        (lang, label) for lang, pairs in meta_tools._METRIC_LABELS.items() for _k, label in pairs
    ],
}


def retired_names(lang: str, text: str) -> list[str]:
    visible = _PLACEHOLDER.sub(" ", text)
    return [p.pattern for p in (_JA_RETIRED if lang == "ja" else _EN_RETIRED) if p.search(visible)]


@pytest.mark.parametrize("table", _VALUES)
def test_no_retired_name_for_the_observation_unit(table):
    offenders = [(lang, names, text[:60]) for lang, text in _VALUES[table] if (names := retired_names(lang, text))]
    assert not offenders, f"{table} uses a retired name for the observation unit: {offenders}"


@pytest.mark.parametrize(
    ("lang", "text"),
    [
        ("ja", "定時率 23.6%、平均遅延 2.5分（観測データ{samples}件・停留所単位）"),
        ("ja", "※少数サンプル"),
        ("en", "{n} samples"),
        ("en", "(low sample)"),
    ],
)
def test_the_guard_catches_what_was_retired(lang, text):
    assert retired_names(lang, text)


@pytest.mark.parametrize(
    ("lang", "text"),
    [
        ("ja", "サンプルデータ"),
        ("ja", "観測数 {samples}件"),
        ("en", "measurement point"),
        ("en", "a sample question"),
    ],
)
def test_the_guard_leaves_legitimate_terms_alone(lang, text):
    assert retired_names(lang, text) == []
