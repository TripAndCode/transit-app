"""Display helpers shared across the query layer: dow_label and time_label."""

from datetime import time

import pytest

from pipeline.query.labels import day_group_label, dow_label, time_label


def test_dow_label_int_maps_to_japanese_char():
    assert dow_label(1) == "月"
    assert dow_label(7) == "日"


def test_dow_label_string_passes_through():
    assert dow_label("平日") == "平日"
    assert dow_label("月") == "月"


def test_dow_label_unknown_int_falls_back_to_str():
    assert dow_label(99) == "99"


@pytest.mark.parametrize(
    ("value", "en"),
    [
        ("平日", "Weekday"),
        ("土曜", "Saturday"),
        ("日祝", "Sunday/Holiday"),
        ("土日祝", "Weekend/Holiday"),
    ],
)
def test_day_group_label_names_the_service_filter_values(value, en):
    """One table names the service filter's values, which the dow reports label
    their rows with too."""
    assert day_group_label(value, lang="en") == en
    assert day_group_label(value, lang="ja") == value
    assert dow_label(value, lang="en") == en


def test_day_group_label_passes_an_unknown_word_through():
    assert day_group_label("秋彼岸", lang="en") == "秋彼岸"


def test_time_label_time_object_renders_hhmm():
    assert time_label(time(8, 0)) == "08:00"
    assert time_label(time(17, 35, 12)) == "17:35"


def test_time_label_string_truncates_to_hhmm():
    """Both 'HH:MM' and 'HH:MM:SS' string forms normalise to 'HH:MM'."""
    assert time_label("08:00") == "08:00"
    assert time_label("08:00:00") == "08:00"


def test_time_label_none_becomes_empty():
    assert time_label(None) == ""
