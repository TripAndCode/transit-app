"""Tests for the i18n-strings linter (stray kana + untranslated JSX attributes)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "frontend" / "scripts" / "lint-i18n-strings.py"
SPEC = importlib.util.spec_from_file_location("lint_i18n_strings", SCRIPT)
assert SPEC and SPEC.loader
lint = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = lint
SPEC.loader.exec_module(lint)


def rules(violations):
    """Reduce violations to the (line, rule) pairs a test cares about."""

    return [(v.line, v.rule) for v in violations]


def test_flags_stray_kana():
    lines = ['const label = "曜日";\n']
    assert rules(lint.find_violations(lines)) == [(1, "kana")]


def test_kana_in_comment_is_ignored():
    lines = ["// 曜日ごとの内訳\n", "const x = 1;\n"]
    assert lint.find_violations(lines) == []


def test_kana_with_i18n_ignore_marker_is_ignored():
    lines = ['const WEEKDAY_KEY = "平日"; // i18n-ignore: GTFS service-type key\n']
    assert lint.find_violations(lines) == []


def test_flags_hardcoded_aria_label():
    lines = ['<button aria-label="More options">\n']
    assert rules(lint.find_violations(lines)) == [(1, "jsx-attribute")]


def test_flags_hardcoded_placeholder():
    lines = ['<input placeholder="Search by email" />\n']
    assert rules(lint.find_violations(lines)) == [(1, "jsx-attribute")]


def test_flags_hardcoded_title_and_alt():
    lines = ['<img alt="Company logo" title="Logo" />\n']
    assert rules(lint.find_violations(lines)) == [(1, "jsx-attribute")]


def test_allows_translated_jsx_attribute_expression():
    lines = ['<button aria-label={t("nav.more_options")}>\n']
    assert lint.find_violations(lines) == []


def test_allows_empty_alt_for_decorative_image():
    lines = ['<img alt="" />\n']
    assert lint.find_violations(lines) == []


def test_jsx_attribute_with_i18n_ignore_marker_is_ignored():
    lines = [
        'const WEEKDAY_KEY = "平日"; // i18n-ignore: GTFS service-type key\n',
        '<button title="OK" /> // i18n-ignore: internal debug only\n',
    ]
    assert lint.find_violations(lines) == []


def test_allows_non_ascii_only_attribute_value():
    # No ASCII letter in the literal -- e.g. a numeric or symbolic value --
    # should not be flagged as untranslated prose.
    lines = ['<div title="42%" />\n']
    assert lint.find_violations(lines) == []
