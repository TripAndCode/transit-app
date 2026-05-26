"""Display helpers shared across the query layer: dow_label and time_label."""

from datetime import time

import pytest

from pipeline.query.labels import dow_label, time_label


# Override the session-scoped DB fixture — pure-Python tests, no DB needed.
@pytest.fixture(scope="session", autouse=True)
def apply_schema():
    yield


def test_dow_label_int_maps_to_japanese_char():
    assert dow_label(1) == "月"
    assert dow_label(7) == "日"


def test_dow_label_string_passes_through():
    assert dow_label("平日") == "平日"
    assert dow_label("月") == "月"


def test_dow_label_unknown_int_falls_back_to_str():
    assert dow_label(99) == "99"


def test_time_label_time_object_renders_hhmm():
    assert time_label(time(8, 0)) == "08:00"
    assert time_label(time(17, 35, 12)) == "17:35"


def test_time_label_string_truncates_to_hhmm():
    """Both 'HH:MM' and 'HH:MM:SS' string forms normalise to 'HH:MM'."""
    assert time_label("08:00") == "08:00"
    assert time_label("08:00:00") == "08:00"


def test_time_label_none_becomes_empty():
    assert time_label(None) == ""
