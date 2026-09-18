"""End-to-end coverage for guard-push-quality.sh's GATE_DIR resolution and
its "zero changed files but the named branch differs" safety net.

test_guard_push_quality_hook.py exercises the embedded Python parser in
isolation; it deliberately does not drive the real bash logic downstream
of that parser, which is where two further defects were found in review
and are pinned here:

- A misresolved GATE_DIR (one that shows no diff against the base branch
  while the actually-named branch does have one) must still be caught by
  the safety net -- this already worked before the parser fix, but stays
  covered here since it is the mechanism the rest of this file's cases
  depend on.
- The safety net's own diff check must use the same --diff-filter=ACMR as
  the scoped PY_FILES/FE_FILES lists it is meant to corroborate. Without
  it, a branch whose only Python/frontend change is a *deletion* is
  wrongly reported as "differs from base" even though the scoped checks
  (correctly) see nothing to lint, and a legitimate push from the
  correct worktree gets refused.

Both cases below avoid ever reaching a real ruff/poetry/npm invocation
(by construction: the misresolved case's own GATE_DIR shows no file to
check, and the deletion-only case's diff-filter makes PY_FILES/FE_FILES
empty too), so this file needs no provisioned virtualenv/node_modules --
only git and bash, which every environment running the suite already has.

A literal `HEAD` ref or a completely bare `git push` is NOT covered here:
review established that this safety net structurally cannot verify
either shape (there is no independent branch reference in the command's
own argv to cross-check GATE_DIR against -- see the hook's own comment
above the safety-net loop), so there is nothing correct to assert.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HOOK_PATH = ROOT / ".claude" / "hooks" / "guard-push-quality.sh"


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, f"git {args} failed: {result.stderr}"
    return result


@pytest.fixture
def fake_repo():
    """A throwaway git repo standing in for both $CLAUDE_PROJECT_DIR (the
    main checkout, left on `main`) and, via worktrees added by each test,
    the worktree a push actually comes from."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "repo"
        root.mkdir()
        _git("init", "-q", "-b", "main", cwd=root)
        _git("config", "user.email", "test@example.com", cwd=root)
        _git("config", "user.name", "Test", cwd=root)
        (root / "existing.py").write_text("VALUE = 1\n")
        _git("add", "existing.py", cwd=root)
        _git("commit", "-q", "-m", "initial", cwd=root)
        yield root


def _run_hook(command: str, *, claude_project_dir: Path, cwd: str = "") -> subprocess.CompletedProcess:
    payload = json.dumps({"tool_input": {"command": command}, "cwd": cwd})
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(claude_project_dir)}
    return subprocess.run(
        ["bash", str(HOOK_PATH)],
        input=payload,
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
        # Matches how the real harness invokes this hook: the shell's own
        # cwd sits wherever the session currently is, not necessarily at
        # $CLAUDE_PROJECT_DIR -- pytest's own cwd (this repo, not the fake
        # one) would otherwise make tier 2's `git worktree list` list the
        # wrong repository entirely.
        cwd=claude_project_dir,
    )


def test_misresolved_gate_dir_is_caught_by_the_safety_net(fake_repo):
    """`git -C <dir> push` takes the named directory at face value (tier 1
    of GATE_DIR resolution) -- if that directory doesn't actually hold the
    named branch's changes (main, here), the safety net must refuse the
    push rather than silently checking nothing."""
    _git("branch", "feature", cwd=fake_repo)
    worktree = fake_repo.parent / "feature-worktree"
    _git("worktree", "add", "-q", str(worktree), "feature", cwd=fake_repo)
    (worktree / "existing.py").write_text("VALUE = 2\n")
    _git("add", "existing.py", cwd=worktree)
    _git("commit", "-q", "-m", "change value", cwd=worktree)

    result = _run_hook(
        f"git -C {fake_repo} push origin feature",
        claude_project_dir=fake_repo,
    )
    assert result.returncode == 2, result.stderr
    assert "nothing differs from" in result.stderr
    assert "feature" in result.stderr


def test_explicit_dash_c_to_a_foreign_repo_is_not_overridden_by_a_preceding_cd(fake_repo):
    """Regression: an explicit `-C <other-repo>` on the statement that
    actually carries `push` must be resolved before tiers 2/3 run at all.
    A prior version left GATE_DIR empty when `-C` named a real, existing,
    but unrelated repository, which let a *preceding* `cd` into this
    repo's own worktree silently fill GATE_DIR instead -- checking a
    branch the push never touches while never inspecting the real
    target (the other repo) at all."""
    _git("branch", "feature", cwd=fake_repo)
    worktree = fake_repo.parent / "feature-worktree"
    _git("worktree", "add", "-q", str(worktree), "feature", cwd=fake_repo)
    (worktree / "existing.py").write_text("VALUE = 2\n")
    _git("add", "existing.py", cwd=worktree)
    _git("commit", "-q", "-m", "change value", cwd=worktree)

    other_repo = fake_repo.parent / "other-repo"
    other_repo.mkdir()
    _git("init", "-q", "-b", "main", cwd=other_repo)

    result = _run_hook(
        f"cd {worktree} && git -C {other_repo} push origin some-branch",
        claude_project_dir=fake_repo,
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""


def test_explicit_refspec_resolves_via_its_source_half_with_no_dash_c_or_cd(fake_repo):
    """Regression: `${ref##*:}` (destination half) was used where the
    source half -- the local branch actually being pushed -- was needed,
    so an explicit `src:dst` refspec resolved to a remote-side name that
    isn't a local worktree/branch, and this tier silently found nothing.
    Drives the real tier-2 (ref -> worktree) lookup with no `-C`/`cd` in
    the command at all, so only a correct source-half extraction can
    resolve GATE_DIR here."""
    _git("branch", "local-feature", cwd=fake_repo)
    worktree = fake_repo.parent / "refspec-worktree"
    _git("worktree", "add", "-q", str(worktree), "local-feature", cwd=fake_repo)
    (worktree / "existing.py").write_text("VALUE = 3\n")
    _git("add", "existing.py", cwd=worktree)
    _git("commit", "-q", "-m", "change value", cwd=worktree)

    result = _run_hook(
        "git push origin local-feature:renamed-remote-branch",
        claude_project_dir=fake_repo,
    )
    # Only care that GATE_DIR resolved to the worktree holding the local
    # branch (the banner only prints when GATE_DIR != CLAUDE_PROJECT_DIR);
    # what happens afterward depends on a provisioned poetry/ruff
    # environment this throwaway repo deliberately doesn't have. Matched
    # on the worktree's directory name rather than its full path: git
    # canonicalizes a linked worktree's path (resolving a macOS /var ->
    # /private/var symlink) in a way tempfile's own Path does not, so a
    # byte-exact path comparison would be comparing two spellings of the
    # same directory.
    banner = result.stderr.splitlines()[0] if result.stderr else ""
    assert banner.startswith("== push gate: files from"), result.stderr
    assert worktree.name in banner, result.stderr


def test_deletion_only_branch_from_the_correct_worktree_is_not_blocked(fake_repo):
    """Regression: the safety net's own diff used to omit
    --diff-filter=ACMR, so a branch whose only Python change is a file
    deletion (which the scoped PY_FILES list correctly excludes) was
    wrongly reported as differing from base -- refusing a legitimate push
    from the directory that actually holds the branch."""
    _git("branch", "drop-file", cwd=fake_repo)
    worktree = fake_repo.parent / "drop-worktree"
    _git("worktree", "add", "-q", str(worktree), "drop-file", cwd=fake_repo)
    _git("rm", "-q", "existing.py", cwd=worktree)
    _git("commit", "-q", "-m", "drop existing.py", cwd=worktree)

    result = _run_hook(
        f"git -C {worktree} push origin drop-file",
        claude_project_dir=fake_repo,
    )
    assert result.returncode == 0, result.stderr
