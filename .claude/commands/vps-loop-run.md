---
name: vps-loop-run
description: Coordinator for the autonomous VPS loop — checks repo/PR state, dispatches an isolated worker sub-agent for the next actionable NEXT_TASK.md backlog item, verifies via /review-branch, and only then pushes/opens a PR.
---

Coordinator for one autonomous-loop run, per
`docs/superpowers/specs/2026-08-27-vps-loop-coordinator-worker-verifier-design.md`.
Backlog lives in `NEXT_TASK.md` at the repo root. Follow the steps in order; never
skip ahead.

## Step 1 — State check

`git status --porcelain`. If NOT empty:
1. Save that output (step 3 of this list needs it; `git stash push` empties it).
2. `git stash push -m "vps-loop-run: unexpected state found at $(date -u +%Y-%m-%dT%H:%M:%SZ)"`.
3. Append to `NEXT_TASK.md`'s "Status log": `- <UTC timestamp>: found unexpected
   uncommitted changes, stashed as <stash ref, e.g. stash@{0}, read from the push
   output> — needs human review before next run. Files: <the exact `git status
   --porcelain` output, one path per line>.` Include the file list verbatim — it's
   the only diagnostic a human gets without SSHing in to inspect the stash.
4. Stop. No later step runs this tick.

## Step 2 — Sync main

`git checkout main && git pull`.

## Step 3 — Pick the next actionable item

Read the "Current task" / "Refactor backlog" sections. Items are numbered; each gets
the fixed branch name `vps-loop/item-<N>` — never derive a name from the item's text.
Walk items top to bottom:

- Skip any item marked "DO NOT START" — intentional, not a failure, not reported as one.
- `gh pr list --search "head:vps-loop/item-<N>" --state all --json number,state`.
  Any `OPEN` or `MERGED` entry → skip item N (done or in progress). A `CLOSED`
  (rejected, unmerged) entry does NOT block it — item N stays eligible.
- If the item text has `Depends on: item <M>`, run the same query for M with
  `--state merged`. Empty → skip N this tick (dependency unmet) and keep walking;
  don't stall the run on it.

First item passing both checks is this run's target.

If none passes (all claimed, blocked, or "DO NOT START"): the loop polls every ~10
minutes, so consecutive idle ticks are expected and must NOT each get a log line.
Read the Status log's last entry: if it already reads "nothing actionable this run"
(timestamp aside), stop silently. Otherwise append `- <UTC timestamp>: nothing
actionable this run.` and stop. Either way, no worker is dispatched.

## Step 3b — Stale worktree/branch before dispatch

Step 3 already confirmed no PR exists for `vps-loop/item-<N>`, and the loop runs one
item at a time, so an existing worktree/branch by that name is leftover from an
interrupted run — which may still hold finished work. Run `git worktree list`; if an
entry's branch is `vps-loop/item-<N>`, check `git log main..vps-loop/item-<N>
--oneline` (from the main checkout, not `-C` into the worktree, so it resolves against
current `main`):

**Log empty** (zero-progress leftover) — discard it:
`git worktree unlock <path>` (ignore "not locked"), `git worktree remove --force
<path>`, `git branch -D vps-loop/item-<N>` if still present, then Step 4.

**Log non-empty** (real commits — the interrupted run may have finished the work):
resume and ship it yourself. Do NOT dispatch an `isolation: "worktree"` worker; that
creates a separate, unrelated worktree.
1. Run Step 5's verification against this existing worktree/branch. Re-verifying is
   cheap and is the trust boundary before anything is pushed, even if the prior run
   already reviewed it once.
2. **Clean (no Major findings):** from the worktree (`git -C <path> ...`)
   `git push -u origin vps-loop/item-<N>`, `gh pr create --draft` (per `pr-github.md`
   style; note in the body that this resumed an interrupted run), fill the real number
   into `docs/refactor-log.md` if it still shows `(PR #pending)`, commit on the same
   branch and push again, `gh pr ready <number>`, then append `- <UTC timestamp>:
   item N shipped as PR #<number> (resumed from an interrupted prior run's existing
   commits).` Run is done — do not also dispatch a new item.
3. **Major findings:** dispatch a plain general-purpose Agent (NOT
   `isolation: "worktree"`) whose prompt tells it to `cd` into the existing worktree
   path first, fix the listed findings there, and commit. Cap at 2 total review
   passes, same as Step 5. Still not clean → append residual findings + worktree path
   to the Status log, no push, no PR, stop.

## Step 4 — Dispatch the worker

Agent tool with `isolation: "worktree"`, `subagent_type` unset (general-purpose).
Give the worker ONLY:

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

Nothing else — not the rest of the backlog, not this command's text, not the design
spec. Record the worktree path and branch the call returns; later steps need them.

## Step 4b — Worker couldn't complete

If the worker reports it did NOT produce a finished local commit (blocked, needs more
context, denied a tool call it couldn't work around), do NOT proceed to Step 5:
append `- <UTC timestamp>: item N blocked before verification — worker reported:
<its blocker, summarized>. Worktree: <path>, branch: vps-loop/item-<N>.`, no push, no
PR, stop.

## Step 5 — Verify

Don't type a bare `/review-branch` and hope it resolves to the worker's branch —
`review-branch.md`'s Boundaries anticipate exactly this ("a subagent's default cwd is
the main repo on `main`"). Follow its process yourself with every git operation run as
`git -C <worktree-abs-path>` against `main`. Compute that diff once and hand its text
to each Phase 2 `branch-reviewer` subagent (scaling the fan-out by diff size per
`review-branch.md`), rather than instructing them to "diff against main".

- No Major findings → Step 6.
- Major findings → dispatch the worker once more (same Agent pattern, same worktree)
  with only those findings, then re-verify. Cap at 2 total passes (matching
  `address-my-pr-comments.md`). Still Major after the second: append `- <UTC
  timestamp>: item N blocked — /review-branch still reports Major findings after 2
  passes. Worktree: <path>, branch: vps-loop/item-<N>. Findings: <summary>.`, no push,
  no PR, stop.

## Step 6 — Ship it

From the worktree (`git -C <worktree-abs-path> ...`):

1. `git push -u origin vps-loop/item-<N>`.
2. `gh pr create --draft`, per `pr-github.md`'s description style. Note the number.
   (CLAUDE.md: every PR starts as a draft until its `/review-branch` pass is clean.)
3. Replace `(PR #pending)` in `docs/refactor-log.md` with `(PR #<number>)`, commit on
   the same branch, push again.
4. `gh pr ready <number>` — Step 5 already came back clean for this exact diff.
5. Append `- <UTC timestamp>: item N shipped as PR #<number>.` to the Status log.

## Boundaries

- Never push to `main`, never force-push, never `git reset --hard`, never delete
  anything found in Step 1 (stash, don't discard).
- Never merge the PR you open — every one waits for explicit human merge approval,
  however clean the verification came back.
- If a step's tool call itself errors (a real tool/dispatch failure, not a Major
  finding), stop and log the error to the Status log with as much detail as available
  (worktree path/branch if one was created) — don't guess or retry blindly.
