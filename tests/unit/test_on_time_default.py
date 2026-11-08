"""The default on-time definition: one source of truth on the server, the
mirror the browser reads, and the one rule that fills a default in.

``frontend/src/api/onTimeDefault.json`` mirrors the server's presets, its
default and the spelling of an unbounded early side, and
``frontend/src/api/onTimeDefault.test.ts`` pins the browser's module to that
file, so moving either side alone fails a test.
"""

import json
from pathlib import Path

import pytest

from pipeline.histogram import LEGACY_SEVERE_LATE_TOLERANCE_SEC, exact_window
from pipeline.reports import definition
from pipeline.reports.comparability import BOARD_ON_TIME_DEFINITION, BOARD_ON_TIME_WINDOW
from pipeline.reports.rankings import (
    DEFAULT_EARLY_DEPARTURE_SEC,
    DEFAULT_PRESET,
    EARLY_ANY,
    ON_TIME_PRESETS,
    early_departure_sec,
    on_time_window,
)

ROOT = Path(__file__).resolve().parents[2]
MIRROR = json.loads((ROOT / "frontend" / "src" / "api" / "onTimeDefault.json").read_text(encoding="utf-8"))
LOCALES = {
    locale: json.loads((ROOT / "frontend" / "src" / "i18n" / "locales" / f"{locale}.json").read_text(encoding="utf-8"))
    for locale in ("ja", "en")
}


def test_the_default_is_the_industry_preset():
    assert DEFAULT_PRESET == "standard_m1_p5"
    assert ON_TIME_PRESETS[DEFAULT_PRESET] == (60, 300)
    assert ON_TIME_PRESETS["legacy_60s"] == (None, 60)


def test_the_browser_mirrors_the_servers_presets_default_and_unbounded_spelling():
    assert MIRROR == {
        "default_preset": DEFAULT_PRESET,
        "early_any": EARLY_ANY,
        "presets": {
            name: {"early_sec": early, "late_sec": late} for name, (early, late) in ON_TIME_PRESETS.items()
        },
    }


@pytest.mark.parametrize(
    ("preset", "early", "late", "window"),
    [
        (None, None, None, (60, 300)),
        ("standard_m1_p5", None, None, (60, 300)),
        ("legacy_60s", None, None, (None, 60)),
        (None, EARLY_ANY, 60, (None, 60)),
        (None, EARLY_ANY, None, (None, 300)),
        (None, None, 180, (60, 180)),
        (None, 120, None, (120, 300)),
        (None, 0, 0, (0, 0)),
    ],
)
def test_each_tolerance_a_request_leaves_out_is_the_default_presets(preset, early, late, window):
    assert on_time_window(preset, early, late) == window


def test_every_preset_reads_the_exact_columns():
    """Each preset, the board's window among them, is read off analyze's
    exact columns, never estimated from a merged histogram."""
    for early, late in ON_TIME_PRESETS.values():
        assert exact_window(early, late)
    assert exact_window(*BOARD_ON_TIME_WINDOW)


def test_the_board_counts_by_the_default_preset():
    assert BOARD_ON_TIME_WINDOW == ON_TIME_PRESETS[DEFAULT_PRESET]
    assert BOARD_ON_TIME_DEFINITION.preset == DEFAULT_PRESET
    assert (BOARD_ON_TIME_DEFINITION.early_tolerance_sec, BOARD_ON_TIME_DEFINITION.late_tolerance_sec) == (60, 300)


def test_the_early_departure_rate_keeps_its_one_minute_threshold():
    """The rate counts departures more than the early tolerance early, one
    minute when every early departure is on time: the default's tolerance is
    that same minute, so the rate's threshold does not move with the default."""
    default_early, _ = on_time_window()
    assert early_departure_sec(default_early) == DEFAULT_EARLY_DEPARTURE_SEC == 60
    legacy_early, _ = ON_TIME_PRESETS["legacy_60s"]
    assert early_departure_sec(legacy_early) == 60


def test_the_default_late_edge_is_the_five_minute_column():
    """worst_5min's count, read from late5_count, is the count past the
    default window's late edge, which its definition names."""
    assert ON_TIME_PRESETS[DEFAULT_PRESET][1] == LEGACY_SEVERE_LATE_TOLERANCE_SEC


@pytest.mark.parametrize("locale", ["ja", "en"])
def test_the_screens_and_the_documents_call_each_preset_by_the_same_name(locale):
    for preset in ON_TIME_PRESETS:
        assert LOCALES[locale]["definitionMeta"]["preset"][preset] == definition._PRESET_TEXT[(preset, locale)]
