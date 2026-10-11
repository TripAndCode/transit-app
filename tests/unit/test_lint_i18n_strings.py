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


def test_a_url_in_the_line_does_not_disable_the_rest_of_it():
    """`//` inside a string is not a comment.

    Splitting on the first one truncates any line carrying a URL, and the
    attribute check then never sees what follows -- which in JSX is exactly
    where `alt`/`title` sit, next to the `href` or `src` they describe.
    """
    lines = ['<a href="https://example.com/x" alt="Hardcoded">link</a>']
    assert rules(lint.find_violations(lines)) == [(1, "jsx-attribute")]


def test_a_real_trailing_comment_is_still_stripped():
    """The negative control: quote tracking must not swallow the whole line,
    or kana parked in a trailing comment starts failing the lint."""
    lines = ["<div>{label}</div>  // 日本語のコメント"]
    assert lint.find_violations(lines) == []


def test_flags_string_literal_inside_braces():
    lines = [
        '<button aria-label={"Close dialog"}>\n',
        "<button title={`Open`}>\n",
        "<input placeholder={'Search'} />\n",
        '<button aria-label={ "Close dialog" }>\n',
    ]
    assert rules(lint.find_violations(lines)) == [(i, "jsx-attribute") for i in range(1, 5)]


def test_flags_other_aria_text_attributes():
    lines = [
        '<div aria-description="Details" />\n',
        '<div aria-roledescription="Slide" />\n',
        '<div aria-valuetext="Half full" />\n',
        '<div aria-placeholder="Pick one" />\n',
    ]
    assert rules(lint.find_violations(lines)) == [(i, "jsx-attribute") for i in range(1, 5)]


def test_allows_template_with_interpolation_and_call_expressions():
    lines = [
        "<button title={`${t(a)} ${b}`}>\n",
        '<button aria-label={cond ? t("a") : t("b")}>\n',
        "<button aria-label={label}>\n",
    ]
    assert lint.find_violations(lines) == []


def test_non_text_aria_attributes_are_not_flagged():
    lines = ['<div aria-live="polite" aria-hidden="true" aria-orientation="vertical" />\n']
    assert lint.find_violations(lines) == []


def test_flags_capitalised_english_jsx_text():
    lines = ["<button>Save</button>\n", "<p>No results found.</p>\n", "<span>Open dialog</span>\n"]
    assert rules(lint.find_violations(lines)) == [(i, "jsx-text") for i in range(1, 4)]


def test_allows_jsx_text_that_is_not_prose():
    lines = [
        "<span>{t('a')}</span>\n",
        "<span>42%</span>\n",
        "<span>{count} items</span>\n",
        "<span>·</span>\n",
        "const x = useState<string>('A');\n",
        "type P = Array<Foo>;\n",
    ]
    assert lint.find_violations(lines) == []


def test_jsx_text_with_i18n_ignore_marker_is_ignored():
    lines = ["<option>Gemini</option> {/* i18n-ignore: brand name */}\n"]
    assert lint.find_violations(lines) == []
