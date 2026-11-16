"""The server-side reader-facing text names the counted unit one way.

Mirrors the frontend glossary guard (``tests/frontend/i18n_glossary.test.mjs``)
for the template tables a reader sees through Ask, the council text, the digest
and the forecast and weather disclaimers. The unit is an observation (観測数);
the retired names stay out of every locale's values.
"""

import re

import pytest

from pipeline.digest import render as digest_render
from pipeline.query import formatter, tools
from pipeline.reports import forecast, weather

_JA_RETIRED = [
    re.compile(r"観測データ数"),
    re.compile(r"サンプル"),
    re.compile(r"計測"),
]
_EN_RETIRED = [
    re.compile(r"\bsamples?\b", re.IGNORECASE),
    re.compile(r"\bmeasurements?\b", re.IGNORECASE),
]

# A `{samples}` placeholder is a field name, not the word a reader sees.
_PLACEHOLDER = re.compile(r"\{[^}]*\}")

_VALUES = {
    "tools._LOCALES": [(lang, text) for (_key, lang), text in tools._LOCALES.items()],
    "formatter._LOCALES": [(lang, text) for (_key, lang), text in formatter._LOCALES.items()],
    "digest.render._DIGEST_LOCALES": [(lang, text) for (_key, lang), text in digest_render._DIGEST_LOCALES.items()],
    "forecast._DISCLAIMER": [(lang, text) for (_key, lang), text in forecast._DISCLAIMER.items()],
    "weather._DISCLAIMER": list(weather._DISCLAIMER.items()),
}


@pytest.mark.parametrize("table", _VALUES)
def test_no_retired_name_for_the_observation_unit(table):
    offenders = []
    for lang, text in _VALUES[table]:
        for pattern in _JA_RETIRED if lang == "ja" else _EN_RETIRED:
            if pattern.search(_PLACEHOLDER.sub("", text)):
                offenders.append((lang, pattern.pattern, text[:60]))
    assert not offenders, f"{table} uses a retired name for the observation unit: {offenders}"
