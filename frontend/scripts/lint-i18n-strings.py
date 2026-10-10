#!/usr/bin/env python3
"""
Lint script to catch hardcoded, non-translated UI text in TypeScript/TSX
source: stray Japanese kana/kanji, literal JSX accessibility/text attributes
(aria-label and the other aria text attributes, placeholder, alt, title; a
quoted string or a string literal inside braces), and capitalised English
text between single-line JSX tags. Every check is suppressed by an
`i18n-ignore` marker on the same line.
Exits with 0 if no matches found, 1 if matches found.
"""

import re
import sys
from dataclasses import dataclass
from pathlib import Path

# Regex pattern for hiragana + katakana + Han (CJK Unified Ideographs)
# Hiragana: U+3040–U+309F
# Katakana: U+30A0–U+30FF
# Han: U+4E00–U+9FFF
KANA_PATTERN = re.compile(r"[぀-ゟ゠-ヿ一-鿿]")

# Attributes whose value is read by users or assistive technology.
_TEXT_ATTRS = (
    r"aria-label|aria-description|aria-roledescription|aria-valuetext|"
    r"aria-placeholder|aria-braillelabel|aria-brailleroledescription|"
    r"placeholder|alt|title"
)

# A hardcoded literal JSX attribute value on one of those attributes, either
# a plain quoted string (`aria-label="More options"`) or a string literal
# wrapped in an expression container (`title={"Open"}`, ``title={`Open`}``).
# Any other expression (`title={label}`, `title={t("k")}`, a template with
# `${}`, a ternary) is routed through a variable or `t()` and is not matched.
# Requires at least one ASCII letter so an empty or purely symbolic value
# (e.g. `alt=""`) doesn't false-positive.
JSX_ATTR_PATTERN = re.compile(
    rf"""\b(?:{_TEXT_ATTRS})=(?:
        (["'])[^"']*[A-Za-z][^"']*\1
        |
        \{{\s*(["`'])[^"'`$]*[A-Za-z][^"'`$]*\2\s*\}}
    )""",
    re.VERBOSE,
)

# Capitalised English prose as the whole text node of a single-line element,
# e.g. `<button>Save</button>`. Text containing an expression (`{...}`) or
# starting lowercase/symbolic is not matched, so interpolated labels and
# punctuation glyphs pass; multi-line text nodes are out of scope for this
# line-oriented lint.
JSX_TEXT_PATTERN = re.compile(r">\s*[A-Z][a-z][^<>{}]*</")

#: Lines that are pure comments — `//`, `*` (JSDoc body), `/*` openers.
COMMENT_LINE_RE = re.compile(r"^\s*(//|\*|/\*)")


def strip_line_comment(line: str) -> str:
    """Return `line` up to its `//` comment, ignoring one inside a string.

    Splitting on the first `//` truncates any line containing a URL, so
    `<a href="https://x" alt="Hardcoded">` loses everything after the
    scheme and the attribute check never sees it. Quote tracking is enough
    here: this is a line-oriented lint over TS/TSX, not a parser.
    """
    quote = None
    i = 0
    while i < len(line):
        ch = line[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in "\"'`":
            quote = ch
        elif ch == "/" and line[i + 1 : i + 2] == "/":
            return line[:i]
        i += 1
    return line


@dataclass(frozen=True)
class Violation:
    line: int
    text: str
    rule: str


def find_violations(lines: list[str]) -> list[Violation]:
    """Check a file's lines for untranslated UI text.

    Comment-only lines are skipped — text in comments never reaches the UI.
    Any `i18n-ignore` marker on the line suppresses it.
    """
    violations = []
    for line_num, line in enumerate(lines, 1):
        if "i18n-ignore" in line:
            continue
        if COMMENT_LINE_RE.match(line):
            continue
        code = strip_line_comment(line)
        if KANA_PATTERN.search(code):
            violations.append(Violation(line_num, line.rstrip(), "kana"))
        elif JSX_ATTR_PATTERN.search(code):
            violations.append(Violation(line_num, line.rstrip(), "jsx-attribute"))
        elif JSX_TEXT_PATTERN.search(code):
            violations.append(Violation(line_num, line.rstrip(), "jsx-text"))
    return violations


def lint_file(file_path) -> list[Violation]:
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return find_violations(f.readlines())
    except Exception as e:
        print(f"Error reading {file_path}: {e}", file=sys.stderr)
        return []


def main():
    src_dir = Path("src")
    if not src_dir.exists():
        print(f"Error: {src_dir} not found", file=sys.stderr)
        sys.exit(1)

    # Find all .ts and .tsx files
    ts_files = list(src_dir.glob("**/*.ts")) + list(src_dir.glob("**/*.tsx"))

    all_matches = []
    for file_path in sorted(ts_files):
        # Skip locale sources (i18n/locales/*.json, i18n/design.ts) and test files
        # (test names may quote UI labels)
        if "i18n/locales" in str(file_path) or str(file_path) == "src/i18n/design.ts" or ".test." in file_path.name:
            continue

        for violation in lint_file(file_path):
            all_matches.append((file_path, violation))
            print(f"{file_path}:{violation.line}: [{violation.rule}] {violation.text}")

    # Exit with 1 if matches found, 0 otherwise
    sys.exit(1 if all_matches else 0)


if __name__ == "__main__":
    main()
