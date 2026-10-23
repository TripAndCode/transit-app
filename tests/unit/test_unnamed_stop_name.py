"""The name a stop goes by when stops.txt gives it none, in each response language."""

import pytest

from pipeline.query.tools import unnamed_stop_name


@pytest.mark.parametrize(("locale", "expected"), [("ja", "2番停留所"), ("en", "Stop #2")])
def test_unnamed_stop_name_speaks_the_request_language(locale, expected):
    assert unnamed_stop_name(2, locale) == expected


def test_unnamed_stop_name_falls_back_to_ja_for_an_unsupported_locale():
    assert unnamed_stop_name(7, "fr") == "7番停留所"
