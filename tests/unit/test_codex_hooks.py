"""Codex's local hook configuration must enforce the shared database and shared-clone boundaries."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HOOKS = json.loads((ROOT / ".codex" / "hooks.json").read_text())
PRE_TOOL_USE = HOOKS["hooks"]["PreToolUse"][0]


def _hook_command(script: str) -> str:
    """The registered command for the hook whose script is `script`, found by name so
    the order of the entries can change without the tests running a different hook."""
    return next(h["command"] for h in PRE_TOOL_USE["hooks"] if script in h["command"])


def _run_guard(command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("bash", "-c", _hook_command("guard-dev-db.sh")),
        cwd=ROOT,
        input=json.dumps({"tool_input": {"command": command}}),
        capture_output=True,
        text=True,
        check=False,
    )


def test_codex_db_hook_blocks_dev_write():
    result = _run_guard("psql postgresql://transit@localhost:5433/transit -c 'DELETE FROM updates'")

    assert result.returncode == 2
    assert "BLOCKED" in result.stderr


def test_codex_db_hook_allows_throwaway_write():
    result = _run_guard("psql postgresql://transit@localhost:5544/transit_test -c 'DELETE FROM updates'")

    assert result.returncode == 0


def _run_git_guard(command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("bash", "-c", _hook_command("guard-git-shared.sh")),
        cwd=ROOT,
        input=json.dumps({"tool_input": {"command": command}}),
        capture_output=True,
        text=True,
        check=False,
    )


def test_codex_git_hook_blocks_a_push_to_main():
    result = _run_git_guard("git push origin HEAD:main")

    assert result.returncode == 2
    assert "BLOCKED" in result.stderr


def test_codex_git_hook_allows_a_branch_push():
    result = _run_git_guard("git push -u origin feature")

    assert result.returncode == 0, result.stderr


def _repo_with_feature_worktree(tmp_path: Path) -> tuple[Path, Path]:
    """A main checkout carrying the Codex wrapper and a stand-in gate that
    prints the checkout it was handed, plus one feature worktree."""

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(("git", "init", "-q", "-b", "main", str(repo)), check=True)
    subprocess.run(("git", "-C", str(repo), "config", "user.name", "Hook Test"), check=True)
    subprocess.run(("git", "-C", str(repo), "config", "user.email", "hook@example.com"), check=True)
    wrapper = repo / ".codex" / "hooks" / "pre-push.sh"
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text((ROOT / ".codex" / "hooks" / "pre-push.sh").read_text())
    guard = repo / ".claude" / "hooks" / "guard-push-quality.sh"
    guard.parent.mkdir(parents=True)
    guard.write_text('printf "%s" "$CLAUDE_PROJECT_DIR"\n')
    subprocess.run(("git", "-C", str(repo), "add", "."), check=True)
    subprocess.run(("git", "-C", str(repo), "commit", "-qm", "hooks"), check=True)
    feature = tmp_path / "feature"
    subprocess.run(("git", "-C", str(repo), "worktree", "add", "-qb", "feature", str(feature)), check=True)
    return repo, feature


def _run_push_hook(cwd: Path, command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("bash", "-c", _hook_command("pre-push.sh")),
        cwd=cwd,
        input=json.dumps({"tool_input": {"command": command}}),
        capture_output=True,
        text=True,
        check=False,
    )


def test_codex_push_hook_uses_main_checkout_from_feature_worktree(tmp_path: Path):
    repo, feature = _repo_with_feature_worktree(tmp_path)

    result = _run_push_hook(feature, "git push origin feature")

    assert PRE_TOOL_USE["matcher"] == "^Bash$"
    assert result.returncode == 0, result.stderr
    assert result.stdout == str(repo)


def test_codex_push_hook_finds_main_checkout_while_it_is_off_main(tmp_path: Path):
    repo, feature = _repo_with_feature_worktree(tmp_path)
    subprocess.run(("git", "-C", str(repo), "checkout", "-q", "--detach"), check=True)

    result = _run_push_hook(feature, "git push origin feature")

    assert result.returncode == 0, result.stderr
    assert result.stdout == str(repo)


def test_codex_push_hook_leaves_other_commands_before_the_gate(tmp_path: Path):
    _, feature = _repo_with_feature_worktree(tmp_path)

    result = _run_push_hook(feature, "git status")

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
