"""Codex's local hook configuration must enforce the shared database boundary."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HOOKS = json.loads((ROOT / ".codex" / "hooks.json").read_text())
PRE_TOOL_USE = HOOKS["hooks"]["PreToolUse"][0]


def _run_guard(command: str) -> subprocess.CompletedProcess[str]:
    configured_command = PRE_TOOL_USE["hooks"][0]["command"]
    return subprocess.run(
        ("bash", "-c", configured_command),
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


def test_codex_push_hook_uses_main_checkout_from_feature_worktree(tmp_path: Path):
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

    result = subprocess.run(
        ("bash", "-c", PRE_TOOL_USE["hooks"][1]["command"]),
        cwd=feature,
        input=json.dumps({"tool_input": {"command": "git push origin feature"}}),
        capture_output=True,
        text=True,
        check=False,
    )

    assert PRE_TOOL_USE["matcher"] == "^Bash$"
    assert result.returncode == 0, result.stderr
    assert result.stdout == str(repo)
