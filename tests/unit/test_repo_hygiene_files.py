"""Pure filesystem assertions for the repo-hygiene files added in this batch
(SECURITY.md, PR template, dependabot config, .gitattributes). No DB access,
so this belongs under `tests/unit/` per CLAUDE.md's convention.
"""

from __future__ import annotations

from pathlib import Path

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


def test_dependabot_config_covers_npm_pip_and_actions():
    path = REPO_ROOT / ".github" / "dependabot.yml"
    assert path.is_file()
    content = path.read_text()
    assert 'package-ecosystem: "npm"' in content or "package-ecosystem: npm" in content
    assert 'package-ecosystem: "pip"' in content or "package-ecosystem: pip" in content
    assert 'package-ecosystem: "github-actions"' in content or "package-ecosystem: github-actions" in content


def test_gitattributes_normalizes_line_endings_and_marks_binaries():
    path = REPO_ROOT / ".gitattributes"
    assert path.is_file()
    content = path.read_text()
    assert "* text=auto eol=lf" in content
    for pattern in ("*.zip binary", "*.pb binary", "*.bin binary", "*.png binary", "*.jpg binary"):
        assert pattern in content
