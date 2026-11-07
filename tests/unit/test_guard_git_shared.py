"""Behavioural tests for the shared-clone git guard (`.claude/hooks/guard-git-shared.sh`).

Several sessions share this clone: one stash stack, one `main`, one remote. The
hook refuses the git commands AGENTS.md forbids outright -- a push that lands on
`main`, a force-push without `--force-with-lease`, and `git stash pop`/`drop`/
`clear` -- and lets every other git command through, including prose that merely
names one of them. Every case runs through the shell entry point settings.json
registers. The commands are JSON payloads fed to the hook's stdin; nothing here
executes them.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HOOK = ROOT / ".claude" / "hooks" / "guard-git-shared.sh"

BLOCKED = [
    # The stash stack is one per clone, whichever worktree runs the command.
    pytest.param("git stash pop", id="stash-pop"),
    pytest.param("git stash pop --index", id="stash-pop-flag"),
    pytest.param("git stash drop", id="stash-drop"),
    pytest.param("git stash drop 'stash@{1}'", id="stash-drop-ref"),
    pytest.param("git stash clear", id="stash-clear"),
    pytest.param("git -C .worktrees/x stash pop", id="stash-pop-dash-C"),
    pytest.param("git --no-pager stash drop", id="stash-drop-global-flag"),
    pytest.param("cd .worktrees/x && git stash pop", id="stash-pop-after-cd"),
    pytest.param("make check && git stash pop", id="stash-pop-second-command"),
    pytest.param('bash -c "git stash pop"', id="stash-pop-inside-bash-c"),
    pytest.param("sh -c 'git stash drop'", id="stash-drop-inside-sh-c"),
    pytest.param('eval "git stash clear"', id="stash-clear-inside-eval"),
    pytest.param("git fetch origin\ngit stash pop", id="stash-pop-on-second-line"),
    # `main` only moves by squash-merge.
    pytest.param("git push origin main", id="push-main"),
    pytest.param("git push -u origin main", id="push-main-upstream"),
    pytest.param("git push origin HEAD:main", id="push-head-to-main"),
    pytest.param("git push origin feature:main", id="push-branch-to-main"),
    pytest.param("git push origin feature:refs/heads/main", id="push-branch-to-refs-heads-main"),
    pytest.param("git push origin refs/heads/main", id="push-refs-heads-main"),
    pytest.param("git push origin main:main", id="push-main-to-main"),
    pytest.param("git push origin :main", id="push-delete-main-refspec"),
    pytest.param("git push --delete origin main", id="push-delete-main-flag"),
    pytest.param("git push origin +main", id="push-plus-main"),
    pytest.param("git push --force-with-lease origin main", id="push-main-with-lease"),
    pytest.param("git push --all origin", id="push-all"),
    pytest.param("git push --mirror origin", id="push-mirror"),
    pytest.param("git push --mirr origin", id="push-mirror-abbreviated"),
    pytest.param("git push origin 'refs/heads/*:refs/heads/*'", id="push-wildcard-refspec"),
    pytest.param("git push origin :", id="push-matching-refspec"),
    pytest.param("git push origin feature main", id="push-main-among-several"),
    pytest.param("git push --repo=origin main", id="push-main-repo-flag-glued"),
    pytest.param("git push --repo origin main", id="push-main-repo-flag"),
    pytest.param("git push origin main 2>&1 | tail -3", id="push-main-piped"),
    pytest.param("git -C /tmp/elsewhere push origin main", id="push-main-dash-C"),
    pytest.param('bash -c "git push origin main"', id="push-main-inside-bash-c"),
    # A force-push can drop commits another session pushed; only the lease form is safe.
    pytest.param("git push --force origin feature", id="force"),
    pytest.param("git push -f origin feature", id="force-short"),
    pytest.param("git push -fu origin feature", id="force-in-short-cluster"),
    pytest.param("git push origin +feature", id="force-plus-refspec"),
    pytest.param("git push origin +HEAD:feature", id="force-plus-head-refspec"),
    pytest.param("git push --force --force-with-lease origin feature", id="force-overrides-lease"),
    # Unlexable text (an apostrophe) is read line by line for the same shapes.
    pytest.param("echo don't; git stash pop", id="stash-pop-after-unbalanced-quote"),
    pytest.param("echo don't && git push origin main", id="push-main-after-unbalanced-quote"),
]

ALLOWED = [
    pytest.param("git stash", id="stash-push-bare"),
    pytest.param("git stash push -m wip", id="stash-push"),
    pytest.param("git stash list", id="stash-list"),
    pytest.param("git stash show -p 'stash@{0}'", id="stash-show"),
    pytest.param("git stash apply 'stash@{0}'", id="stash-apply"),
    pytest.param("git stash branch rescue 'stash@{0}'", id="stash-branch"),
    pytest.param("git push origin feature", id="push-branch"),
    pytest.param("git push -u origin chore/agents", id="push-branch-upstream"),
    pytest.param("git push origin HEAD", id="push-head"),
    pytest.param("git push origin HEAD:feature", id="push-head-to-branch"),
    pytest.param("git push --force-with-lease origin feature", id="push-with-lease"),
    pytest.param("git push --force-with-lease=feature:abc123 origin feature", id="push-with-lease-expected"),
    pytest.param("git push --force-if-includes --force-with-lease origin feature", id="push-with-lease-and-includes"),
    pytest.param("git push --delete origin feature", id="push-delete-branch"),
    pytest.param("git push origin :feature", id="push-delete-branch-refspec"),
    pytest.param("git push origin maintenance", id="push-branch-named-like-main"),
    pytest.param("git push origin main-fix", id="push-branch-prefixed-main"),
    pytest.param("git push origin fix-main", id="push-branch-suffixed-main"),
    pytest.param("git push origin feature:refs/heads/mainline", id="push-to-ref-prefixed-main"),
    pytest.param("git push -o ci.skip origin feature", id="push-option-value"),
    pytest.param("git push -oci.skip origin feature", id="push-option-value-attached"),
    pytest.param("git push --push-option=ci.skip origin feature", id="push-option-long-glued"),
    pytest.param("git -C .worktrees/x push origin x", id="push-branch-dash-C"),
    pytest.param("git push origin feature 2>&1 | tail -3", id="push-branch-piped"),
    pytest.param("git log --oneline main..HEAD", id="log-main-range"),
    pytest.param("git diff --stat main...feature", id="diff-main"),
    pytest.param("git fetch origin main", id="fetch-main"),
    pytest.param("git pull --ff-only origin main", id="pull-main"),
    pytest.param("git merge --ff-only origin/main", id="merge-origin-main"),
    pytest.param("git checkout main", id="checkout-main"),
    pytest.param("git switch main", id="switch-main"),
    pytest.param("git rebase main", id="rebase-main"),
    pytest.param("git branch --show-current", id="branch-show-current"),
    pytest.param("git worktree add .worktrees/x -b x origin/main", id="worktree-add"),
    pytest.param("gh pr create --base main --draft", id="gh-pr-create"),
    pytest.param("gh pr merge 1 --squash", id="gh-pr-merge"),
    pytest.param("python3 scripts/cleanup_git_state.py --apply", id="cleanup-script"),
    # Prose that names a forbidden command is not the command.
    pytest.param('git commit -m "docs: never git stash pop in a shared clone"', id="commit-message-prose"),
    pytest.param('git commit -m "ci: git push origin main is blocked"', id="commit-message-push-main"),
    pytest.param('grep -rn "git stash pop" AGENTS.md', id="grep-prose"),
    pytest.param('echo "git push origin main"', id="echo-prose"),
    pytest.param("git log --grep 'stash pop'", id="log-grep-prose"),
    pytest.param("echo don't && git status", id="unbalanced-quote-no-git-write"),
    pytest.param("git push origin feature && echo it's main", id="unbalanced-quote-prose-after-push"),
    pytest.param("echo don't; git push origin feature # main", id="unbalanced-quote-comment-after-push"),
    pytest.param("make check", id="no-git"),
    pytest.param("", id="empty"),
]


def _run(command: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    payload: dict[str, object] = {"tool_input": {"command": command}}
    if cwd is not None:
        payload["cwd"] = str(cwd)
    return subprocess.run([str(HOOK)], input=json.dumps(payload), text=True, capture_output=True)


@pytest.mark.parametrize("command", BLOCKED)
def test_shared_clone_damage_is_blocked(command, tmp_path: Path):
    # `tmp_path` holds no repository, so none of these verdicts depends on the branch
    # the test process happens to run from.
    result = _run(command, cwd=tmp_path)
    assert result.returncode == 2, result.stderr
    assert "BLOCKED" in result.stderr


@pytest.mark.parametrize("command", ALLOWED)
def test_other_git_commands_are_allowed(command, tmp_path: Path):
    result = _run(command, cwd=tmp_path)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("payload", ["", "not json", '{"tool_input": "git stash pop"}', "[]"])
def test_unreadable_input_is_refused(payload):
    result = subprocess.run([str(HOOK)], input=payload, text=True, capture_output=True)
    assert result.returncode == 2


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(("git", "-C", str(cwd), *args), check=True, capture_output=True, timeout=15)


@pytest.fixture
def clone(tmp_path: Path) -> tuple[Path, Path]:
    """A repository with `main` checked out and a `feature` worktree beside it."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(("git", "init", "-q", "-b", "main", str(repo)), check=True, capture_output=True)
    _git(repo, "config", "user.name", "Guard Test")
    _git(repo, "config", "user.email", "guard@example.com")
    (repo / "README").write_text("x\n")
    _git(repo, "add", "README")
    _git(repo, "commit", "-qm", "init")
    feature = tmp_path / "feature"
    _git(repo, "worktree", "add", "-qb", "feature", str(feature))
    return repo, feature


def test_bare_push_while_main_is_checked_out_is_blocked(clone):
    repo, _ = clone
    result = _run("git push", cwd=repo)
    assert result.returncode == 2
    assert "main" in result.stderr


def test_bare_push_to_a_named_remote_while_on_main_is_blocked(clone):
    repo, _ = clone
    result = _run("git push origin", cwd=repo)
    assert result.returncode == 2


def test_bare_push_from_a_feature_worktree_is_allowed(clone):
    _, feature = clone
    result = _run("git push", cwd=feature)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("spec", ["HEAD", "@", "HEAD:refs/heads/main"])
def test_pushing_the_checked_out_branch_by_name_reads_the_checkout(clone, spec):
    repo, feature = clone
    assert _run(f"git push origin {spec}", cwd=repo).returncode == 2
    allowed_from_feature = spec != "HEAD:refs/heads/main"
    assert (_run(f"git push origin {spec}", cwd=feature).returncode == 0) is allowed_from_feature


def test_a_tags_only_push_moves_no_branch(clone):
    repo, _ = clone
    assert _run("git push --tags origin", cwd=repo).returncode == 0
    assert _run("git push --follow-tags origin", cwd=repo).returncode == 2


def test_the_line_by_line_fallback_reads_the_checkout_too(clone):
    repo, feature = clone
    assert _run("echo don't; git push", cwd=repo).returncode == 2
    assert _run("echo don't; git push", cwd=feature).returncode == 0


def test_dash_C_decides_which_checkout_a_bare_push_reads(clone):
    repo, feature = clone
    assert _run(f"git -C {repo} push origin", cwd=feature).returncode == 2
    assert _run(f"git -C {feature} push origin", cwd=repo).returncode == 0


def test_a_preceding_cd_decides_which_checkout_a_bare_push_reads(clone):
    repo, feature = clone
    assert _run(f"cd {repo} && git push", cwd=feature).returncode == 2
    assert _run(f"cd {feature} && git push", cwd=repo).returncode == 0


def test_bare_push_outside_a_repository_is_allowed(tmp_path: Path):
    result = _run("git push", cwd=tmp_path)
    assert result.returncode == 0, result.stderr


def _bash_hook_commands(config: Path) -> list[str]:
    hooks = json.loads(config.read_text())["hooks"]["PreToolUse"]
    return [h["command"] for matcher in hooks if "Bash" in matcher["matcher"] for h in matcher["hooks"]]


def test_the_guard_is_registered_for_claude_code():
    assert any("guard-git-shared.sh" in c for c in _bash_hook_commands(ROOT / ".claude" / "settings.json"))


def test_the_guard_is_registered_for_codex():
    assert any("guard-git-shared.sh" in c for c in _bash_hook_commands(ROOT / ".codex" / "hooks.json"))
