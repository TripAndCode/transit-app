"""``api.sqlutil.escape_like`` escapes the three characters that carry
special meaning in a Postgres ``LIKE``/``ILIKE`` pattern (``%``, ``_``) plus
the escape character itself (``\\``), so a caller-supplied substring used to
build a ``%...%`` pattern can never smuggle in its own wildcard.
"""

from api.sqlutil import escape_like


def test_escape_like_escapes_percent():
    assert escape_like("50%") == r"50\%"


def test_escape_like_escapes_underscore():
    assert escape_like("a_b") == r"a\_b"


def test_escape_like_escapes_backslash():
    assert escape_like("a\\b") == r"a\\b"


def test_escape_like_escapes_backslash_before_other_specials():
    # Escaping order matters: backslash must be escaped first, or a raw "%"
    # in the input would be double-escaped once its own leading "\" is added.
    assert escape_like("100%_off") == r"100\%\_off"


def test_escape_like_leaves_plain_text_untouched():
    assert escape_like("hello world") == "hello world"


def test_escape_like_empty_string():
    assert escape_like("") == ""
