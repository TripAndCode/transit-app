"""The frontend lint gate's two zero-tolerance settings.

Both were relaxed on purpose once, while `any` and warn-level findings were
being worked off, and both are one small edit from being relaxed again. A
reverted ratchet fails silently: lint still exits 0, so nothing surfaces
until the findings have accumulated again.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE_JSON = ROOT / "frontend" / "package.json"
ESLINT_CONFIG = ROOT / "frontend" / "eslint.config.js"


def test_lint_script_fails_on_any_warning():
    """`eslint .` alone exits 0 with warnings, so a warn-level rule — the
    React Compiler bailout signals among them — reports nothing that blocks
    a merge."""
    scripts = json.loads(PACKAGE_JSON.read_text())["scripts"]
    assert "--max-warnings 0" in scripts["lint"], f"the lint script no longer fails on warnings: {scripts['lint']!r}"


def test_explicit_any_is_an_error_not_a_warning():
    """At 'warn' this rule stops blocking the moment --max-warnings is
    dropped, so the two settings have to hold together."""
    config = ESLINT_CONFIG.read_text()
    match = re.search(r"'@typescript-eslint/no-explicit-any':\s*'(\w+)'", config)
    assert match, "no-explicit-any is no longer configured explicitly"
    assert match.group(1) == "error", f"no-explicit-any is set to {match.group(1)!r}, not 'error'"


def test_no_source_file_silences_the_any_rule_inline():
    """A per-line disable is how the rule gets worked around once it starts
    blocking, and it leaves no trace in the lint output."""
    offenders = [
        path.relative_to(ROOT)
        for path in (ROOT / "frontend" / "src").rglob("*.ts*")
        if "no-explicit-any" in path.read_text()
    ]
    assert not offenders, f"these silence the any rule inline instead of typing the value: {offenders}"
