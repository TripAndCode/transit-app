#!/usr/bin/env python3
"""
Lint script to detect stray hardcoded Japanese kana in TypeScript/TSX files.
Exits with 0 if no matches found, 1 if matches found.
"""

import re
import sys
from pathlib import Path

# Regex pattern for hiragana + katakana + Han (CJK Unified Ideographs)
# Hiragana: U+3040–U+309F
# Katakana: U+30A0–U+30FF
# Han: U+4E00–U+9FFF
KANA_PATTERN = re.compile(r"[぀-ゟ゠-ヿ一-鿿]")


#: Lines that are pure comments — `//`, `*` (JSDoc body), `/*` openers.
COMMENT_LINE_RE = re.compile(r"^\s*(//|\*|/\*)")


def lint_file(file_path):
    """Check file for stray kana. Returns list of (line_num, line_content) tuples.

    Comment-only lines are skipped — kana in comments never reaches the UI.
    Any `i18n-ignore` marker (line or JSX comment) suppresses the line.
    """
    matches = []
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            for line_num, line in enumerate(f, 1):
                # Skip lines with i18n-ignore marker (// or {/* */} form)
                if "i18n-ignore" in line:
                    continue
                # Skip comment-only lines; strip trailing // comments
                if COMMENT_LINE_RE.match(line):
                    continue
                code = line.split("//", 1)[0]
                # Check for kana
                if KANA_PATTERN.search(code):
                    matches.append((line_num, line.rstrip()))
    except Exception as e:
        print(f"Error reading {file_path}: {e}", file=sys.stderr)
    return matches


def main():
    src_dir = Path("src")
    if not src_dir.exists():
        print(f"Error: {src_dir} not found", file=sys.stderr)
        sys.exit(1)

    # Find all .ts and .tsx files
    ts_files = list(src_dir.glob("**/*.ts")) + list(src_dir.glob("**/*.tsx"))

    all_matches = []
    for file_path in sorted(ts_files):
        # Skip i18n/locales directory and test files (test names may quote UI labels)
        if "i18n/locales" in str(file_path) or ".test." in file_path.name:
            continue

        matches = lint_file(file_path)
        if matches:
            for line_num, line_content in matches:
                all_matches.append((file_path, line_num, line_content))
                print(f"{file_path}:{line_num}: {line_content}")

    # Exit with 1 if matches found, 0 otherwise
    sys.exit(1 if all_matches else 0)


if __name__ == "__main__":
    main()
