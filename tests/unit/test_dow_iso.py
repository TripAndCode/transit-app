"""ISODOW maps: Japanese day char <-> ISO weekday number (Mon=1..Sun=7)."""

from pipeline.db import _DOW_ISO_TO_JP, _DOW_JP_TO_ISO


def test_jp_to_iso_monday_is_one():
    assert _DOW_JP_TO_ISO["月"] == 1


def test_jp_to_iso_sunday_is_seven():
    assert _DOW_JP_TO_ISO["日"] == 7


def test_iso_to_jp_round_trips_every_day():
    for jp, iso in _DOW_JP_TO_ISO.items():
        assert _DOW_ISO_TO_JP[iso] == jp


def test_iso_to_jp_covers_full_week():
    assert sorted(_DOW_ISO_TO_JP.keys()) == [1, 2, 3, 4, 5, 6, 7]
