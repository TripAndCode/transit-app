---
name: vps-loop-run
description: Coordinator for the autonomous VPS loop — checks repo/PR state, dispatches an isolated worker sub-agent for the next actionable NEXT_TASK.md backlog item, verifies via /review-branch, and only then pushes/opens a PR.
---

Coordinator for one autonomous-loop run. (A design spec exists at
`docs/superpowers/specs/2026-08-27-vps-loop-coordinator-worker-verifier-design.md`, but
`docs/` is gitignored — it won't exist on the VPS clone. This file is self-contained;
don't block on reading it.) Backlog lives in `NEXT_TASK.md` at the repo root. Follow the steps in order; never
skip ahead.

## Step 1 — State check

`git status --porcelain --untracked-files=no` — **tracked changes only**. `NEXT_TASK.md`
lives untracked at the repo root by design, so an unfiltered `git status --porcelain` is
permanently non-empty and would stop every tick forever. If NOT empty:
1. Save that output (step 3 of this list needs it; `git stash push` empties it).
2. `git stash push -m "vps-loop-run: unexpected state found at $(date -u +%Y-%m-%dT%H:%M:%SZ)"`.
   If it reports "No local changes to save" (nothing was stashed), log the file list
   with no stash ref instead of inventing one.
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

If none passes (all claimed, blocked, or "DO NOT START"): consecutive idle ticks are
expected at the loop's configured cron cadence (see CLAUDE.md ▸ Autonomous VPS loop —
don't restate a number here) and must NOT each get a log line.
Read the Status log's last entry: if it already reads "nothing actionable this run"
(timestamp aside), stop silently. Otherwise append `- <UTC timestamp>: nothing
actionable this run.` and stop. Either way, no worker is dispatched.

## Step 3b — Stale worktree/branch before dispatch

Step 3 already confirmed no PR exists for `vps-loop/item-<N>`, and the loop runs one
item at a time (its cron cadence must exceed a worst-case tick, or this inference
doesn't hold), so an existing worktree or local branch by that name is leftover from an
interrupted run — which may still hold finished work.

Check both: `git worktree list`, and `git rev-parse --verify vps-loop/item-<N>` for a
branch with no worktree. If either exists, check `git log main..vps-loop/item-<N>
--oneline` (from the main checkout, not `-C` into the worktree, so it resolves against
current `main`):

**Log empty — no commits beyond `main`.** Before discarding anything, check the
worktree itself: `git -C <path> status --porcelain`.
- Dirty (the likeliest interrupted state is a worker killed mid-implementation, before
  its first commit — and `remove --force` exists precisely to override that refusal):
  do NOT remove it. `git -C <path> stash push -u -m "vps-loop item-<N> leftover"`, log
  the stash ref, worktree path, and file list to the Status log, and stop this tick.
  Boundaries say stash, don't discard — and Step 1's check never looks inside a
  worktree.
- Clean: `git worktree unlock <path>` (ignore "not locked"), `git worktree remove
  --force <path>`, `git branch -D vps-loop/item-<N>` if still present, then Step 4.

**Log non-empty — real commits exist** (the interrupted run may have finished the
work): resume and ship it yourself. Do NOT dispatch an `isolation: "worktree"` worker;
that creates a separate, unrelated worktree.
1. Run only Step 5's **review** process against this worktree/branch. Step 5's own
   Major-findings branch does NOT apply here — it dispatches the Step 4
   `isolation: "worktree"` pattern; use item 3 below instead. Re-verifying is cheap and
   is the trust boundary before anything is pushed, even if the prior run reviewed it.
2. **Clean (no Major findings):** run **Step 6** as written, noting in the PR body that
   this resumed an interrupted prior run, and use the resumed variant of the status
   line: `- <UTC timestamp>: item N shipped as PR #<number> (resumed from an interrupted
   prior run's existing commits).` This run is done — do not also dispatch a new item.
3. **Major findings:** dispatch a plain general-purpose Agent (NOT
   `isolation: "worktree"`) whose prompt tells it to `cd` into the existing worktree
   path first, fix the listed findings there, and commit. Cap at 2 total fix iterations,
   same as Step 5. Clean after that → do item 2. Still not clean → append residual
   findings + worktree path to the Status log, no push, no PR, stop.

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
`git -C <worktree-abs-path>`.

Compute the diff once **into a file**, exactly per `review-branch.md` Phase 1 —
including its `:(top)` pathspecs and its lockfile/secret exclusions — and hand each
Phase 2 `branch-reviewer` subagent that file's **absolute path**, the changed-file
list, the objective, its dimension(s), and the worktree path. Never paste diff text
into the prompts: unattended runs hit the largest diffs, where inlining is both the
most expensive and the most likely to truncate silently. Scale the fan-out per
`review-branch.md`'s ladder and escalations.

- No Major findings → Step 6.
- Major findings → dispatch the worker once more (same Agent pattern, same worktree)
  with only those findings, then re-verify. Cap at 2 **fix iterations** (a distinct
  counter from `review-branch.md`'s 3 fresh-eyes gate passes; `address-my-pr-comments.md`
  uses the same cap). Still Major after the second: append `- <UTC timestamp>: item N
  blocked — /review-branch still reports Major findings after 2 fix iterations.
  Worktree: <path>, branch: vps-loop/item-<N>. Findings: <summary>.`, no push, no PR,
  stop.

## Step 6 — Ship it

From the worktree (`git -C <worktree-abs-path> ...`):

1. `git push -u origin vps-loop/item-<N>`.
2. `gh pr create --draft`, per `pr-github.md`'s description style. Note the number.
   (CLAUDE.md: every PR starts as a draft until its `/review-branch` pass is clean.)
3. Replace `(PR #pending)` in `docs/refactor-log.md` with `(PR #<number>)`, commit on
   the same branch, push again.
4. Promotion out of draft depends on how many **fresh-eyes gate passes** ran.
   `review-branch.md` defines ready-for-merge as three of them, and root CLAUDE.md
   applies that identically to loop work. Step 5 runs one. So: leave the PR in **draft**
   and log `- <UTC timestamp>: item N — 1 of 3 gate passes run; needs human completion
   before merge.` Run `gh pr ready <number>` only if all three passes ran clean in this
   tick.
5. Append `- <UTC timestamp>: item N shipped as PR #<number>.` to the Status log.

## Boundaries

- Never push to `main`, never force-push, never `git reset --hard`, never delete
  anything found in Step 1 (stash, don't discard).
- Never merge the PR you open — every one waits for explicit human merge approval,
  however clean the verification came back.
- If a step's tool call itself errors (a real tool/dispatch failure, not a Major
  finding), stop and log the error to the Status log with as much detail as available
  (worktree path/branch if one was created) — don't guess or retry blindly.
