"""Tests for the deterministic branch-review manifest builder."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "prepare_review.py"
SPEC = importlib.util.spec_from_file_location("prepare_review", SCRIPT)
assert SPEC and SPEC.loader
prepare_review = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = prepare_review
SPEC.loader.exec_module(prepare_review)


def git(repo: Path, *args: str) -> str:
    """Run Git in a temporary fixture repository."""

    return subprocess.run(
        ("git", "-C", str(repo), *args),
        check=True,
        capture_output=True,
        text=True,
    ).stdout


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    """Create a repository with a main branch and one committed source file."""

    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "Review Test")
    git(repo, "config", "user.email", "review@example.com")
    (repo / "app.py").write_text("value = 1\n", encoding="utf-8")
    git(repo, "add", "app.py")
    git(repo, "commit", "-m", "initial")
    return repo


def run_script(repo: Path, output_dir: Path, *extra_args: str) -> dict[str, object]:
    """Execute the script as users do and decode its manifest."""

    result = subprocess.run(
        (
            sys.executable,
            str(SCRIPT),
            "--repo",
            str(repo),
            "--base",
            "main",
            "--output-dir",
            str(output_dir),
            *extra_args,
        ),
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def test_manifest_combines_committed_and_local_changes(repository: Path, tmp_path: Path):
    """Committed, staged, unstaged, and untracked files share one review artifact."""

    git(repository, "switch", "-c", "feature")
    (repository / "app.py").write_text("value = 2\n", encoding="utf-8")
    git(repository, "add", "app.py")
    git(repository, "commit", "-m", "change app")
    (repository / "notes.md").write_text("notes\n", encoding="utf-8")

    manifest = run_script(repository, tmp_path / "artifacts")

    assert manifest["changed_files"] == ["app.py", "notes.md"]
    assert manifest["changed_lines"] == 3
    assert manifest["suggested_tier"] == "standard"
    diff = Path(str(manifest["diff_path"])).read_text(encoding="utf-8")
    assert "value = 2" in diff
    assert "notes" in diff


def test_sensitive_files_are_named_but_never_serialized(repository: Path, tmp_path: Path):
    """Credential-bearing paths stay visible without leaking their contents."""

    git(repository, "switch", "-c", "feature")
    (repository / ".env").write_text("TOKEN=do-not-serialize\n", encoding="utf-8")
    (repository / "guide.md").write_text("safe\n", encoding="utf-8")
    git(repository, "add", "-f", ".env", "guide.md")
    git(repository, "commit", "-m", "add guide")

    manifest = run_script(repository, tmp_path / "artifacts")

    assert manifest["changed_files"] == ["guide.md"]
    assert manifest["deliberately_excluded"] == [".env"]
    diff = Path(str(manifest["diff_path"])).read_text(encoding="utf-8")
    assert "do-not-serialize" not in diff
    assert "safe" in diff


def test_additional_exclusion_is_applied_before_diff_creation(repository: Path, tmp_path: Path):
    """A caller can withhold a newly recognized credential carrier."""

    secret_path = repository / "vendor-access.txt"
    secret_path.write_text("temporary credential material\n", encoding="utf-8")

    manifest = run_script(
        repository,
        tmp_path / "artifacts",
        "--exclude",
        "vendor-access.txt",
    )

    assert manifest["changed_files"] == []
    assert manifest["deliberately_excluded"] == ["vendor-access.txt"]
    diff = Path(str(manifest["diff_path"])).read_text(encoding="utf-8")
    assert diff == ""


def test_worktree_reversal_is_not_double_counted(repository: Path, tmp_path: Path):
    """The artifact represents final content, not a sequence of opposing patches."""

    git(repository, "switch", "-c", "feature")
    (repository / "app.py").write_text("value = 2\n", encoding="utf-8")
    git(repository, "add", "app.py")
    git(repository, "commit", "-m", "temporary change")
    (repository / "app.py").write_text("value = 1\n", encoding="utf-8")

    manifest = run_script(repository, tmp_path / "artifacts")

    assert manifest["changed_files"] == []
    assert manifest["changed_lines"] == 0
    diff = Path(str(manifest["diff_path"])).read_text(encoding="utf-8")
    assert diff == ""


def test_process_doc_and_enforcement_flags_are_deterministic(repository: Path, tmp_path: Path):
    """Path-only routing metadata does not require an LLM to recalculate it."""

    settings = repository / ".claude" / "settings.json"
    settings.parent.mkdir()
    settings.write_text("{}\n", encoding="utf-8")

    manifest = run_script(repository, tmp_path / "artifacts")

    assert manifest["suggested_tier"] == "process-doc"
    assert manifest["enforcement"] is True


def test_entry_chunk_quality_gate_script_is_flagged_as_enforcement(repository: Path, tmp_path: Path):
    """frontend/scripts/check-entry-chunk.mjs enforces "MapLibre stays out of
    the entry chunk" -- a real quality gate outside the top-level scripts/
    directory and the settings/hooks/CI paths touches_enforcement already
    recognized, so a diff touching only this file must still route as
    enforcement (extra review), not silently fall through as an ordinary
    change."""

    gate = repository / "frontend" / "scripts" / "check-entry-chunk.mjs"
    gate.parent.mkdir(parents=True)
    gate.write_text("// placeholder gate\n", encoding="utf-8")

    manifest = run_script(repository, tmp_path / "artifacts")

    assert manifest["changed_files"] == ["frontend/scripts/check-entry-chunk.mjs"]
    assert manifest["enforcement"] is True


def test_output_directory_inside_repository_is_rejected(repository: Path):
    """Review artifacts cannot recursively become part of a later review diff."""

    result = subprocess.run(
        (
            sys.executable,
            str(SCRIPT),
            "--repo",
            str(repository),
            "--output-dir",
            str(repository / "artifacts"),
        ),
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "must be outside" in result.stderr
