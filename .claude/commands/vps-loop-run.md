---
name: vps-loop-run
description: Coordinator for the autonomous VPS loop — checks repo/PR state, dispatches an isolated worker sub-agent for the next actionable NEXT_TASK.md backlog item, verifies via /review-branch, and only then pushes/opens a PR.
---

Coordinator for one autonomous-loop run. (A design spec exists at
`docs/superpowers/specs/2026-08-27-vps-loop-coordinator-worker-verifier-design.md`;
`docs/superpowers/**` IS gitignored, so it won't exist on the VPS clone — this file is
self-contained, don't block on reading it. Note `docs/refactor-log.md` is *not* ignored:
`.gitignore` negates it and it's tracked, so Steps 4 and 6 can and must write it.) Backlog lives in `NEXT_TASK.md` at the repo root. Follow the steps in order; never
skip ahead.

## Step 1 — State check

`git status --porcelain -- ':(top)' ':(exclude,top)NEXT_TASK.md'` — everything except
the one file that is untracked by design. (`NEXT_TASK.md` isn't gitignored, so an
unfiltered `git status --porcelain` is permanently non-empty and would stop every tick
forever — but blanket `--untracked-files=no` would also blind this guard to a killed
worker's leftovers or a copied-in `*.pem`, none of which are ignored here.) If NOT
empty:
1. Save that output (step 3 of this list needs it; `git stash push` empties it).
2. `git stash push -u -m "vps-loop-run: unexpected state found at $(date -u +%Y-%m-%dT%H:%M:%SZ)" -- ':(top)' ':(exclude,top)NEXT_TASK.md'`
   — `-u` so untracked leftovers are saved too (matching Step 3b), and the same
   `NEXT_TASK.md` exclusion as the status check above: a bare `stash push -u` would
   stash the loop's own untracked input file out of the worktree, leaving every later
   tick with no backlog to read. Two outcomes that
   create no stash — log the file list with no stash ref rather than inventing one, and
   say which occurred: "No local changes to save", or `Entry '<path>' not uptodate.
   Cannot merge.` (an intent-to-add index entry someone left behind; note it needs
   `git reset -- <path>` by a human, and do NOT run that yourself).
3. Append to `NEXT_TASK.md`'s "Status log": `- <UTC timestamp>: found unexpected
   uncommitted changes, stashed as <stash ref, e.g. stash@{0}, read from the push
   output> — needs human review before next run. Files: <the exact output of the
   filtered `git status --porcelain` from above, one path per line>.` Include the file list verbatim — it's
   the only diagnostic a human gets without SSHing in to inspect the stash.
4. Stop. No later step runs this tick.

## Step 2 — Sync main and clear proven-stale state

1. `git checkout main && git pull --ff-only`.
2. Run `python3 scripts/cleanup_git_state.py` and inspect its complete plan, then run
   `python3 scripts/cleanup_git_state.py --apply`. This is the canonical cleanup
   policy shared with `/cleanup-merged`: it removes only clean local worktrees/refs
   that are already recoverable from `main` or an exact merged-PR head. It retains
   open PRs, dirty/locked worktrees, unique commits, `production`, and remote branches.
3. If either command errors, follow the Boundaries tool-error rule: log it and stop
   this tick. Never replace a retained decision with manual force deletion.

## Step 3 — Pick the next actionable item

Read the "Current task" / "Refactor backlog" sections. Items are numbered; each gets
the fixed branch name `vps-loop/item-<N>` — never derive a name from the item's text.
Walk items top to bottom:

- Skip any item marked "DO NOT START" — intentional, not a failure, not reported as one.
- `gh pr list --head "vps-loop/item-<N>" --state all --json number,state,headRefName`,
  and assert `headRefName` equals the branch exactly before acting. Use `--head`, NOT
  `--search "head:…"`: the search qualifier matches by prefix/token, so
  `head:vps-loop/item-1` can also return `item-10`'s PR and skip item 1 forever with no
  log line. Any `OPEN` or `MERGED` entry → skip item N (done or in progress). A
  `CLOSED` (rejected, unmerged) entry does NOT block a fresh attempt — but see Step 3b
  before resuming any leftover commits.
- If the item text has `Depends on: item <M>`, run the same exact-head query for M with
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

A worktree or local branch named `vps-loop/item-<N>` at this point is leftover from an
interrupted run (the loop runs one item at a time — its cron cadence must exceed a
worst-case tick, or this inference doesn't hold) **or** from a PR a human closed, since
Step 3 keeps `CLOSED` items eligible.

**First: was its PR rejected?** Re-query `gh pr list --head "vps-loop/item-<N>" --state
closed --json number,state,headRefName`. A `CLOSED`, unmerged PR means a human declined
these exact commits — do NOT resume and re-push them. Log
`- <UTC timestamp>: item N has leftover commits from closed PR #<number>; not resumed —
delete the branch or reopen the PR by hand.` and skip item N this tick.

Otherwise detect both shapes: `git worktree list`, and
`git rev-parse --verify --quiet refs/heads/vps-loop/item-<N>` for a branch with no
worktree (use `--quiet` and the full ref — a bare `rev-parse --verify` prints
`fatal: Needed a single revision` and exits 128 on the normal no-branch path, which
Boundaries would treat as a tool error and stop the tick). Then check
`git log main..vps-loop/item-<N> --oneline` (from the main checkout, not `-C` into the
worktree, so it resolves against current `main`).

**Log empty — no commits beyond `main`:**
- *Branch only, no worktree:* nothing can be lost — `git branch -D vps-loop/item-<N>`,
  then Step 4.
- *Worktree exists:* check it before discarding anything —
  `git -C <path> status --porcelain`.
  - Dirty (the likeliest interrupted state is a worker killed mid-implementation,
    before its first commit — and `remove --force` exists precisely to override that
    refusal): do NOT remove it. `git -C <path> stash push -u -m "vps-loop item-<N>
    leftover"`, log the stash ref, worktree path, and file list, and stop this tick.
    Boundaries say stash, don't discard — and Step 1's check never looks inside a
    worktree.
  - Clean: `git worktree unlock <path>` (ignore "not locked"), `git worktree remove
    --force <path>`, `git branch -D vps-loop/item-<N>` if still present, then Step 4.

**Log non-empty — real commits exist** and no closed PR rejected them:
- *Branch only, no worktree:* do NOT review from the main checkout — Step 2 just left it
  on `main`, so `git diff main...HEAD` there is empty and would produce a vacuously
  clean review of unreviewed commits. Log `- <UTC timestamp>: item N has an
  un-reviewed leftover branch with commits but no worktree: <commit list>. Not resumed
  — needs a human to attach a worktree or delete it.` and stop this tick.
- *Worktree exists:* resume and ship it yourself. Do NOT dispatch an
  `isolation: "worktree"` worker; that creates a separate, unrelated worktree.
  1. Run only Step 5's **review** process against this worktree/branch. Step 5's own
     Major-findings branch does NOT apply here — it dispatches the Step 4
     `isolation: "worktree"` pattern; use item 3 below instead. Re-verifying is cheap
     and is the trust boundary before anything is pushed.
  2. **Clean (no Major findings):** run **Step 6** as written, with two adjustments:
     note in the PR body that this resumed an interrupted prior run, and replace Step
     6's item-5 status line with `- <UTC timestamp>: item N shipped as PR #<number>
     (resumed from an interrupted prior run's existing commits).` Step 6's item 3 is
     conditional — an interrupted worker may never have written the `(PR #pending)`
     placeholder. This run is done; do not also dispatch a new item.
  3. **Major findings:** dispatch a plain general-purpose Agent (NOT
     `isolation: "worktree"`) whose prompt tells it to `cd` into the existing worktree
     path first, fix the listed findings there, and commit. Cap at 2 fix iterations,
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
  the missing prerequisite yourself. Never run `git stash drop`, `git stash
  clear`, or `git stash pop` against any stash you did not create yourself
  in this exact tick — stashes are visible repo-wide across worktrees, so
  one you find may be another operator's or a prior tick's leftover
  explicitly held for human review. If you notice a stash you didn't
  create, leave it untouched and mention it in your report instead of
  inspecting, reusing, or discarding it."

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

Run `scripts/prepare_review.py` against the worker worktree, then follow
`review-branch.md`'s current routing, prompt, synthesis, and verification rules. That
command is the canonical home for review fan-out and retry policy; do not copy them
here. Add the worktree absolute path to every dispatch and never paste diff text into
the prompts.

- No Major findings → Step 6.
- Major findings → dispatch the worker once more (same Agent pattern, same worktree)
  with only those findings, then re-verify only the affected review group. Cap at 2
  fix iterations. Still Major after the second: append `- <UTC timestamp>: item N
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
4. **Leave the PR in draft.** Step 5 completed the required proportional review, but
   the unattended loop never marks its own PR ready; that remains the human's action.
5. Append: `- <UTC timestamp>: item N opened as draft PR #<number>; automated review
   complete, needs human approval before merge.`

## Boundaries

- Never push to `main`, never force-push, never `git reset --hard`, never delete
  anything found in Step 1 (stash, don't discard).
- Never merge the PR you open — every one waits for explicit human merge approval,
  however clean the verification came back.
- If a step's tool call itself errors (a real tool/dispatch failure, not a Major
  finding), stop and log the error to the Status log with as much detail as available
  (worktree path/branch if one was created) — don't guess or retry blindly.
