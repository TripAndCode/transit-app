"""The gitleaks version must stay identical everywhere it's pinned.

.pre-commit-config.yaml's `rev`, .github/workflows/secrets-scan.yml's
GITLEAKS_VERSION, and scripts/setup_git_hooks.sh's GITLEAKS_VERSION each
pin the same gitleaks release independently (a pre-commit `rev:` can't
reference a shell variable, so there is no single file all three can read
from). If they drift, the local hook, CI, and `make bootstrap`/`make
hooks`'s installer would each scan with a different ruleset/binary, silently
breaking the "local hook output matches CI output" guarantee the configs'
own comments promise.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _pre_commit_rev() -> str:
    text = (ROOT / ".pre-commit-config.yaml").read_text()
    match = re.search(r"^\s*rev:\s*v([0-9.]+)\s*$", text, re.MULTILINE)
    assert match, ".pre-commit-config.yaml: could not find a gitleaks `rev:` pin"
    return match.group(1)


def _ci_workflow_version() -> str:
    text = (ROOT / ".github" / "workflows" / "secrets-scan.yml").read_text()
    match = re.search(r"^\s*GITLEAKS_VERSION:\s*([0-9.]+)\s*$", text, re.MULTILINE)
    assert match, "secrets-scan.yml: could not find a GITLEAKS_VERSION pin"
    return match.group(1)


def _setup_script_version() -> str:
    text = (ROOT / "scripts" / "setup_git_hooks.sh").read_text()
    match = re.search(r'^GITLEAKS_VERSION="([0-9.]+)"\s*$', text, re.MULTILINE)
    assert match, "setup_git_hooks.sh: could not find a GITLEAKS_VERSION pin"
    return match.group(1)


def test_gitleaks_version_pins_match_across_config_and_ci():
    pre_commit = _pre_commit_rev()
    ci = _ci_workflow_version()
    setup_script = _setup_script_version()
    assert pre_commit == ci == setup_script, (
        f"gitleaks version pins have drifted apart: "
        f".pre-commit-config.yaml={pre_commit!r}, "
        f"secrets-scan.yml={ci!r}, setup_git_hooks.sh={setup_script!r}"
    )


def test_pre_commit_config_uses_the_system_hook_not_the_golang_one():
    """The default `gitleaks` hook id is `language: golang`, meaning
    pre-commit builds it from source via a local Go toolchain on first use.
    `gitleaks-system` instead runs the pinned binary already on PATH (see
    scripts/setup_git_hooks.sh), so a workstation/VPS without Go still gets
    a working hook.
    """

    text = (ROOT / ".pre-commit-config.yaml").read_text()
    assert re.search(r"^\s*-\s*id:\s*gitleaks-system\s*$", text, re.MULTILINE), (
        ".pre-commit-config.yaml must use the `gitleaks-system` hook id, not `gitleaks`"
    )
