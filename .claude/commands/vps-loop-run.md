---
name: vps-loop-run
description: Coordinator for the autonomous VPS loop — checks repo/PR state, dispatches an isolated worker sub-agent for the next actionable NEXT_TASK.md backlog item, verifies via /review-branch, and only then pushes/opens a PR.
---

Act as the coordinator for one autonomous-loop run, per
`docs/superpowers/specs/2026-08-27-vps-loop-coordinator-worker-verifier-design.md`.
Read `NEXT_TASK.md` at the repo root for the backlog. Follow every step below
in order; do not skip ahead.

## Step 1 — State check

Run `git status --porcelain`. If it is NOT empty:
1. Save that output — you need it for step 2, and `git stash push` empties
   it.
2. `git stash push -m "vps-loop-run: unexpected state found at $(date -u +%Y-%m-%dT%H:%M:%SZ)"`.
3. Append to `NEXT_TASK.md`'s "Status log" section: `- <UTC timestamp>: found
   unexpected uncommitted changes, stashed as <stash ref> — needs human
   review before next run. Files: <the exact git status --porcelain output
   from step 1, one path per line>.` (read the exact stash ref, e.g.
   `stash@{0}`, from the `git stash push` output). This file list is the
   only diagnostic a human gets without SSHing in to inspect the stash
   themselves — include it verbatim, don't summarize or omit it.
4. Stop here. Do not proceed to any later step this run.

## Step 2 — Sync main

`git checkout main && git pull`.

## Step 3 — Pick the next actionable item

Read the backlog in `NEXT_TASK.md` (the "Current task" / "Refactor backlog"
sections). Items are numbered; each gets the fixed branch name
`vps-loop/item-<N>` — never derive a name from the item's own text. Walk
items top to bottom. For each item N, in order:

- Skip any item explicitly marked "DO NOT START" — that's intentional, not a
  failure, and isn't reported as one.
- Run `gh pr list --search "head:vps-loop/item-<N>" --state all --json
  number,state`. Inspect the returned `state` field(s): if any entry has
  state `OPEN` or `MERGED`, skip item N — already done or in progress. A
  `CLOSED` (rejected, unmerged) entry does NOT block item N — it remains
  eligible for a fresh attempt on this run.
- If the item's text includes a line `Depends on: item <M>`, run
  `gh pr list --search "head:vps-loop/item-<M>" --state merged --json
  number`. If that's empty (item M's PR isn't merged yet), skip item N for
  now — its dependency isn't satisfied yet. Keep walking; don't stall the
  whole run on it.

The first item N that passes both checks is this run's target.

If no item passes (every remaining item is already claimed, blocked on an
unmet dependency, or flagged "DO NOT START"), append to the Status log:
`- <UTC timestamp>: nothing actionable this run.` and stop.

## Step 3b — Clean up a stale worktree/branch before dispatching

By the time Step 3 selects item N, it has already confirmed no PR exists
for `vps-loop/item-<N>`. Since this loop runs strictly one item at a time
(hourly cron, never overlapping), a worktree or local branch already
named `vps-loop/item-<N>` at this point is leftover from an interrupted
prior run — but "interrupted" doesn't mean "worthless." Check which case this
is before doing anything:

Run `git worktree list`. If an entry's branch is `vps-loop/item-<N>`:

1. Check `git log main..vps-loop/item-<N> --oneline` (run this in the main
   checkout, not `-C` into the worktree, so it always resolves against the
   current `main`).
2. **If that log is empty** (no commits beyond `main` — a genuinely
   zero-progress leftover, like an aborted worktree before any work
   happened): it's safe to discard.
   - `git worktree unlock <that path>` (ignore an error saying it wasn't
     locked).
   - `git worktree remove --force <that path>`.
   - `git branch -D vps-loop/item-<N>` if the branch still exists locally.
   - Proceed to Step 4 with a clean slate.
3. **If that log is NOT empty** (real commits exist — the interrupted run
   may have actually finished the implementation, possibly already
   verified): resume and ship it yourself, without dispatching a fresh
   `isolation: "worktree"` worker (that would create an unrelated, separate
   worktree rather than continuing this one).
   a. Run `/review-branch` against this existing worktree/branch, exactly
      as Step 5 does for a freshly-dispatched worker — even if the prior
      run may have already run it once, re-verifying here is cheap and is
      the trust boundary before anything gets pushed.
   b. **Clean (no Major findings):** from the worktree
      (`git -C <that path> ...`), push it (`git push -u origin
      vps-loop/item-<N>`), `gh pr create` for it (per `pr-github.md`
      style, noting in the PR body that this resumed an interrupted prior
      run), fill in the real PR number in `docs/refactor-log.md` if it's
      still showing `(PR #pending)` (commit that on the same branch, push
      again), and append to the Status log:
      `- <UTC timestamp>: item N shipped as PR #<number> (resumed from an
      interrupted prior run's existing commits).` This run is done — do
      not also dispatch a new item.
   c. **Major findings:** dispatch a fix — not via `isolation: "worktree"`
      (which creates a new, disconnected worktree), but a plain
      general-purpose Agent dispatch whose prompt explicitly instructs it
      to `cd` into the existing worktree path first, then fix the listed
      findings there and commit. Cap at 2 total review passes, same as
      Step 5's own cap. If still not clean after 2 passes: append the
      residual findings and the worktree path to the Status log for human
      review, do NOT push, do NOT open a PR, stop.

## Step 4 — Dispatch the worker

Use the Agent tool with `isolation: "worktree"` (leave `subagent_type` unset
— general-purpose). Give the worker ONLY:

- The exact text of backlog item N, copied verbatim from `NEXT_TASK.md`.
- This instruction, verbatim: "Implement this on a new branch named
  `vps-loop/item-<N>`, following this repo's normal CLAUDE.md conventions
  (tests, `make check`-scoped checks, `[skip ci]` in the commit trailer).
  Commit your work locally on that branch, including a one-line append to
  `docs/refactor-log.md` describing what you did (date + summary; leave the
  PR number as `(PR #pending)` — the coordinator fills in the real number
  later). Do NOT push and do NOT open a PR — a separate verification step
  happens after you're done. If you discover you need something not present
  in the current `main` and not described in this task's own text, STOP and
  report that blocker instead of implementing a workaround or reimplementing
  the missing prerequisite yourself."

Do not give the worker the rest of the backlog, this command's own text, or
the design spec — it should see only its one task.

Record the worktree path and branch name the Agent tool call returns; later
steps need them.

## Step 4b — If the worker couldn't complete the task

If the worker's own report indicates it could NOT complete the
implementation (blocked, needs more context, denied a tool call it
couldn't work around, or otherwise did not produce a finished local
commit) — as opposed to reporting a completed implementation ready for
review — do NOT proceed to Step 5. Instead:

1. Append to the Status log: `- <UTC timestamp>: item N blocked before
   verification — worker reported: <the worker's own blocker description,
   summarized>. Worktree: <path>, branch: vps-loop/item-<N>.`
2. Do NOT push, do NOT open a PR.
3. Stop here.

## Step 5 — Verify

Invoke `/review-branch`, but do not rely on the bare slash command
defaulting to the right branch — `review-branch.md`'s own Boundaries note
anticipates exactly this scenario ("a subagent's default cwd is the main
repo on main"). Explicitly follow its process yourself with every git
operation it specifies (`git diff`, `git log`, etc.) substituted with
`git -C <worktree-abs-path>` against `main`, rather than typing a bare
`/review-branch` and hoping it resolves to the worker's branch. Dispatch its
Phase 2 fresh-context `branch-reviewer` subagents exactly as
`review-branch.md` describes, giving each the `git -C <worktree-abs-path>`
diff output (not a bare "diff against main" instruction) so they review the
right branch regardless of their own default cwd.

- No Major findings: continue to Step 6.
- Major findings: dispatch the worker once more (same Agent tool pattern,
  same worktree) with only the specific Major findings to fix. Re-run
  `/review-branch`. If Major findings still remain after this second pass
  (cap at 2 total review passes, matching `address-my-pr-comments.md`'s
  convention):
  - Append to the Status log: `- <UTC timestamp>: item N blocked —
    /review-branch still reports Major findings after 2 passes. Worktree:
    <path>, branch: vps-loop/item-<N>. Findings: <summary>.`
  - Do NOT push, do NOT open a PR.
  - Stop here.

## Step 6 — Ship it

From the worktree (`git -C <worktree-abs-path> ...`):

1. `git push -u origin vps-loop/item-<N>`.
2. `gh pr create` for that branch, following `pr-github.md`'s PR description
   style (structure over prose, affected-scope table, etc.). Note the PR
   number `gh pr create` returns.
3. Replace the `(PR #pending)` placeholder in `docs/refactor-log.md` with
   the real `(PR #<number>)`, commit that change on the same branch, and
   `git -C <worktree-abs-path> push` again (updates the same open PR).
4. Append to `NEXT_TASK.md`'s local "Status log" section:
   `- <UTC timestamp>: item N shipped as PR #<number>.`

## Boundaries

- Never push to `main` directly, never force-push, never `git reset --hard`,
  never delete anything found in Step 1 (stash, don't discard).
- Never merge the PR you open. Every PR from this command waits for
  explicit human merge approval — no exceptions, regardless of how clean the
  verification pass was.
- If any step's tool call itself errors (a real tool/dispatch failure, not a
  Major finding), stop and log the error to the Status log with as much
  detail as available (worktree path/branch if one was created) rather than
  guessing or retrying blindly.
