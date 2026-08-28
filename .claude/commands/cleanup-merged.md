---
name: cleanup-merged
description: Sync main, then safely remove proven-stale local branches and worktrees; optionally repeat in a persistent VPS clone.
---

Run the standard post-merge cleanup. Local cleanup is the default. Optional arguments:

- `--vps <user@host>` repeats the workflow over SSH.
- `--repo <absolute-path>` selects the remote repository (default `/root/transit-app`).
- `--identity <key-path>` adds the SSH identity. Never print or read the key.

## 1. Protect active work

1. Record `git status --porcelain`, the current branch, and `git worktree list
   --porcelain`. Path names may be shown; never print credential-file contents.
2. A dirty worktree is never deleted. `NEXT_TASK.md` may remain untracked in the
   persistent checkout; preserve it exactly.
3. If the current branch is not `main`, query its exact head with `gh pr list --head
   <branch> --state all`. Switch the persistent checkout to `main` only when its PR is
   `MERGED`, no open PR exists, and the checkout has no changes except the preserved
   `NEXT_TASK.md`. Otherwise leave the current branch checked out and continue; the
   cleanup script will retain it.

## 2. Sync the base without rewriting work

1. Run `git fetch --prune origin`.
2. Compare `main...origin/main`. If local `main` is ahead, stop: those commits need
   human inspection. If `main` is checked out in a worktree, require that worktree to
   be clean except for `NEXT_TASK.md`, then fast-forward it with `git merge --ff-only
   origin/main`. If it is not checked out, advance the ref only when it is strictly
   behind `origin/main`.
3. Do not sync `production`; it is a deliberate deployment-promotion branch.

## 3. Plan, apply, verify

Run from the target repository:

```bash
python3 scripts/cleanup_git_state.py
python3 scripts/cleanup_git_state.py --apply
```

The first command is the inspectable dry run. The second may run without another
interactive confirmation when cleanup was the requested task: it rechecks each tip
and worktree immediately before deletion. Stop on any error; never substitute
`--force` worktree removal or hand-delete a directory.

Verify with `git status --porcelain`, `git branch --list`, and `git worktree list
--porcelain`. Report deleted and retained state separately.

## 4. Optional VPS clone

When `--vps` was supplied, repeat Steps 1–3 through one non-interactive SSH session in
the requested remote repo. Shell-quote the repo, target, and identity as data. The
remote clone must already contain `scripts/cleanup_git_state.py`; if it does not, sync
`main` first and stop if the script is still absent.

## Boundaries

- Delete only entries marked `DELETE` by `cleanup_git_state.py`.
- Never delete remote/GitHub branches, push, force-push, reset, stash, or discard files.
- `main`, `production`, the invoking worktree, open PRs, dirty/locked worktrees,
  unmerged work, and local tips that differ from their merged PR head are retained.
- Additional named keepers use `--protect <branch>`; do not weaken the built-in rules.
