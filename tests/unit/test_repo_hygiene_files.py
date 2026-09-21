"""Repo-hygiene files: SECURITY.md, the PR template, the Dependabot config,
.gitattributes, and the local-only trees .gitignore has to keep out.

Filesystem and `git` only, no DB, so this belongs under `tests/unit/` per
CLAUDE.md's convention.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_security_md_exists_and_mentions_reporting():
    path = REPO_ROOT / "SECURITY.md"
    assert path.is_file()
    content = path.read_text()
    assert "security advisory" in content.lower()


def test_pull_request_template_has_origin_field():
    path = REPO_ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md"
    assert path.is_file()
    content = path.read_text()
    assert "**Origin:**" in content


def test_dependabot_config_is_valid_and_covers_npm_pip_and_actions():
    """Parsed, not grepped.

    An unparseable or mis-shaped dependabot.yml is not an error GitHub
    reports anywhere this repo would notice — it simply stops opening update
    PRs. A substring check on the raw text passes in exactly that case, so
    the guard has to go through the YAML.
    """
    path = REPO_ROOT / ".github" / "dependabot.yml"
    assert path.is_file()
    config = yaml.safe_load(path.read_text())

    assert config["version"] == 2
    updates = config["updates"]
    by_ecosystem = {entry["package-ecosystem"]: entry for entry in updates}
    assert set(by_ecosystem) == {"npm", "pip", "github-actions"}

    for ecosystem, entry in by_ecosystem.items():
        assert entry["directory"].startswith("/"), ecosystem
        assert entry["schedule"]["interval"] == "weekly", ecosystem
        # Every group must actually select something; an empty group is
        # accepted by the parser and silently does nothing.
        for name, group in entry.get("groups", {}).items():
            assert group.get("update-types"), f"{ecosystem}: group {name} selects no updates"

    assert by_ecosystem["npm"]["directory"] == "/frontend", "npm manifests live in frontend/"


def test_gitattributes_normalizes_line_endings_and_marks_binaries():
    path = REPO_ROOT / ".gitattributes"
    assert path.is_file()
    content = path.read_text()
    assert "* text=auto eol=lf" in content
    for pattern in ("*.zip binary", "*.pb binary", "*.bin binary", "*.png binary", "*.jpg binary"):
        assert pattern in content


def test_every_tracked_binary_extension_is_declared_binary():
    """`* text=auto eol=lf` asks git to guess for anything not marked binary.

    The guess is good but not free, so every binary extension actually in the
    tree should be declared rather than inferred. This fails when a new binary
    type is committed without a matching .gitattributes line.
    """
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    declared = {
        line.split()[0].removeprefix("*")
        for line in (REPO_ROOT / ".gitattributes").read_text().splitlines()
        if line.strip().endswith(" binary")
    }
    binary_like = {".zip", ".pb", ".bin", ".png", ".jpg", ".jpeg", ".gif", ".pdf", ".ico", ".woff", ".woff2"}
    present = {suffix for suffix in (Path(p).suffix for p in tracked) if suffix in binary_like}
    assert present <= declared, f"tracked binary types missing from .gitattributes: {sorted(present - declared)}"


def _is_ignored(relative_path: str) -> bool:
    """Ask git, rather than reading .gitignore and reimplementing its matching."""
    return (
        subprocess.run(
            ["git", "check-ignore", "-q", "--no-index", relative_path],
            cwd=REPO_ROOT,
        ).returncode
        == 0
    )


def test_local_only_trees_stay_ignored():
    """The blanket `docs/*` rule used to cover everything under docs/.

    With docs/ tracked by default, this enumeration is the only thing keeping
    local planning material and Finder droppings out of a `git add -A`. Asking
    git directly means the test fails when a name is dropped from .gitignore,
    not merely when the file's text changes.
    """
    for path in (
        "docs/superpowers/notes.md",
        "docs/specs/plan.md",
        "docs/references/reference.md",
        "docs/.DS_Store",
        ".DS_Store",
        "frontend/src/.DS_Store",
        ".env",
        ".env.local",
    ):
        assert _is_ignored(path), f"{path} is no longer ignored"


def test_tracked_docs_and_env_example_are_not_ignored():
    """The negative control: a rule broad enough to catch everything above
    must still leave the real docs and .env.example committable."""
    for path in (
        "docs/deploy-railway.md",
        "docs/features/ask-tab.md",
        "docs/refactor-log.md",
        ".env.example",
    ):
        assert not _is_ignored(path), f"{path} is ignored but must stay tracked"
