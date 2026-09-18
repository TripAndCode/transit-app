"""`_copy_field` encodes a value for COPY's TEXT format.

The dedup transfer's correctness rests entirely on this function: it replaces
a row-wise INSERT, where the driver did the adaptation, with a text stream this
code writes by hand.
"""

from datetime import date, datetime, time, timezone

import pytest

from pipeline.analyze import _copy_field


def test_none_is_the_null_sentinel_not_an_empty_string():
    assert _copy_field(None) == "\\N"


def test_empty_string_stays_an_empty_string():
    """The reason this uses TEXT format rather than CSV.

    A CSV unquoted empty field *is* NULL, so '' and None would collapse into
    the same value and a service_type of '' would silently become NULL.
    """
    assert _copy_field("") == ""


@pytest.mark.parametrize(
    "value,expected",
    [
        ("44372", "44372"),
        ("平日", "平日"),
        ("平日_11時37分_系統44372", "平日_11時37分_系統44372"),
        (0, "0"),
        (-300, "-300"),
        (date(2026, 6, 9), "2026-06-09"),
        (time(11, 37), "11:37:00"),
    ],
)
def test_plain_values_render_as_themselves(value, expected):
    assert _copy_field(value) == expected


def test_a_naive_datetime_is_written_as_utc():
    """ClickHouse hands back naive datetimes that mean UTC.

    Without the offset Postgres would read them in the session timezone, which
    every real caller pins to Asia/Tokyo — nine hours early.
    """
    assert _copy_field(datetime(2026, 9, 16, 11, 13, 32)) == "2026-09-16T11:13:32+00:00"


def test_an_aware_datetime_keeps_its_own_offset():
    aware = datetime(2026, 9, 16, 11, 13, 32, tzinfo=timezone.utc)
    assert _copy_field(aware) == "2026-09-16T11:13:32+00:00"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("a\tb", "a\\tb"),
        ("a\nb", "a\\nb"),
        ("a\rb", "a\\rb"),
        ("a\\b", "a\\\\b"),
        # The backslash must be escaped first, or the escape introduced for the
        # tab would itself be re-escaped into a literal backslash plus 't'.
        ("a\\tb", "a\\\\tb"),
        ("\\N", "\\\\N"),
    ],
)
def test_delimiters_and_backslashes_are_escaped(raw, expected):
    assert _copy_field(raw) == expected
