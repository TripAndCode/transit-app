"""LocaleMiddleware Accept-Language parser.

Pins the supported-locale set ({ja, en}), q-value handling, primary-tag
matching, and the JP default fallback. Pure-Python — no DB.
"""

import pytest

from api.middleware.locale import DEFAULT_LOCALE, _pick_locale


# Override the session-scoped DB fixture — pure-Python tests, no DB needed.
@pytest.fixture(scope="session", autouse=True)
def apply_schema():
    yield


def test_pick_locale_missing_header_falls_back_to_default():
    assert _pick_locale(None) == DEFAULT_LOCALE
    assert _pick_locale("") == DEFAULT_LOCALE


def test_pick_locale_simple_match():
    assert _pick_locale("en") == "en"
    assert _pick_locale("ja") == "ja"


def test_pick_locale_primary_subtag_match():
    """``en-US`` and ``ja-JP`` are matched on the primary subtag."""
    assert _pick_locale("en-US") == "en"
    assert _pick_locale("ja-JP") == "ja"


def test_pick_locale_q_values_select_highest():
    """``en;q=0.4,ja;q=0.9`` → ja wins on q-value."""
    assert _pick_locale("en;q=0.4,ja;q=0.9") == "ja"
    assert _pick_locale("ja;q=0.2,en;q=0.8") == "en"


def test_pick_locale_q_zero_skipped():
    """A range with ``q=0`` is ignored, even if listed first."""
    assert _pick_locale("en;q=0,ja;q=0.5") == "ja"


def test_pick_locale_unsupported_falls_back_to_default():
    """Browser asking only for fr-FR gets the JP default, not an error."""
    assert _pick_locale("fr-FR,de;q=0.5") == DEFAULT_LOCALE


def test_pick_locale_prefers_first_on_tie():
    """``en,ja`` (equal q=1) → first wins (en)."""
    assert _pick_locale("en,ja") == "en"
    assert _pick_locale("ja,en") == "ja"


def test_pick_locale_real_browser_string():
    """Chrome-style header: ``en-GB,en;q=0.9,ja;q=0.5`` → en."""
    assert _pick_locale("en-GB,en;q=0.9,ja;q=0.5") == "en"
