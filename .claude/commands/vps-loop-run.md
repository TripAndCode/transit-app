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

## Step 2b — Detect unshipped work stranded behind an already-merged item

Step 3's per-item check treats any `MERGED` PR for `vps-loop/item-<N>` as "done,
skip." That's true for the item's *original* scope, but a worktree/branch can
keep accumulating real commits after its first PR merged — most often a
follow-up `/review-branch` fix cycle on a resumed branch (Step 3b's own resume
path) that itself got interrupted before reaching Step 6. Nothing before this
step ever re-examines an item once Step 3 would call it done, so real,
already-reviewed follow-up work can sit silently stranded in that worktree
indefinitely — every subsequent tick keeps correctly reporting the item as
"MERGED" and moving on without ever looking at the worktree's actual tip.

For every `vps-loop/item-<N>` entry Step 2's `cleanup_git_state.py` run just
retained specifically for the reason **"local tip differs (possible post-merge
commits)"** — not any other KEEP reason, and not items with no such worktree —
check whether that tip actually carries unshipped substance:

1. `git log main..vps-loop/item-<N> --oneline` from the main checkout. A
   squash-merged branch's original commits always show here even when their
   content already landed byte-for-byte on `main` — this list alone does NOT
   prove unshipped work; it only says the tip differs from a squash-merge
   commit hash, which is expected and harmless on its own.
2. For each file `git diff main...vps-loop/item-<N> --stat` shows as touched,
   compare against `main`'s current version of that file (`git show
   origin/main:<path>`) for the *specific thing the branch's commit messages
   describe fixing* — not just "is the diff empty." The longer a branch sits,
   the more likely unrelated later work already absorbed the same fix
   independently; a mechanical non-empty diff is not evidence of a real gap by
   itself. This step requires reading both sides, not a scripted check.
3. **Content already covered on `main` (the common case for an old, stale
   worktree):** append `- <UTC timestamp>: item N's leftover
   vps-loop/item-<N> worktree/branch is fully superseded — every change it
   made is already present on main via later, unrelated work. Removed via
   git worktree remove --force + git branch -D.`, then actually remove the
   worktree and branch. Do not leave it for a future tick to re-discover;
   `cleanup_git_state.py` will not reclaim it on its own since the tip
   genuinely differs from any merged head by its literal commit hash.
4. **Genuinely new, unshipped content confirmed:** item N is NOT done despite
   its merged PR. Append `- <UTC timestamp>: item N's merged PR (#<original
   number>) doesn't cover all commits on vps-loop/item-<N>; unshipped content
   found (<short description>). Routing to Step 3b's resume-and-ship path as
   a follow-up this tick.` and go straight to Step 3b's "real commits exist,
   worktree exists" branch for item N — skip Step 3's normal top-to-bottom
   walk for this tick; other items resume their normal queue position next
   tick once this is resolved.

Skip this step entirely on a tick where Step 2 retained no worktree for this
reason — it only exists to catch the one blind spot described above, not to
re-audit every retained worktree on every tick.

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
  1. Run Step 5's full **Pass 1 then Pass 2** review sequence against this
     worktree/branch — the two-pass rule applies here exactly as it does to a
     freshly-dispatched item; a resumed branch is not exempt. Neither pass's own
     "dispatch the worker" sub-branch applies here, though — both route to the
     Step 4 `isolation: "worktree"` pattern, which doesn't fit a resume; use item
     3 below instead for a Major on either pass. Re-verifying is cheap and is the
     trust boundary before anything is pushed.
  2. **Clean (no Major findings on either pass):** run **Step 6** as written, with
     two adjustments: note in the PR body that this resumed an interrupted prior
     run, and replace Step 6.11's status line with `- <UTC timestamp>: item N
     shipped as PR #<number> (resumed from an interrupted prior run's existing
     commits).` Step 6.4 is conditional — an interrupted worker may never
     have written the `(PR #pending)` placeholder. This run is done; do not also
     dispatch a new item.
  3. **Major findings (either pass):** dispatch a plain general-purpose Agent (NOT
     `isolation: "worktree"`) whose prompt tells it to `cd` into the existing worktree
     path first, fix the listed findings there, and commit. Cap at 2 fix iterations
     per pass, same as Step 5. Clean after that → resume at whichever pass found the
     findings (finish Pass 2 if Pass 1 was the one fixed; if Pass 2 was the one
     fixed, that fix-and-reverify cycle *is* Pass 2 — no further pass needed) before
     doing item 2. Still not clean after that pass's cap → append residual findings
     + worktree path to the Status log, no push, no PR, stop.

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
  clear`, or `git stash pop` against any stash you did not push yourself
  during this session — stashes are visible repo-wide across worktrees, so
  one you find may be another operator's or a prior run's leftover
  explicitly held for human review. If you notice a stash you didn't push,
  leave it untouched and mention it in your report instead of inspecting,
  reusing, or discarding it. If you do pop or drop a stash you pushed
  yourself, confirm it is still the top entry via `git stash list` first
  (or reference it by its exact `stash@{n}`) in case anything else was
  pushed after it."

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

Per CLAUDE.md, every PR gets **at least two full, independent `/review-branch`
invocations** before Step 6 — unconditionally, even when the first finds nothing.
Run them as a strict sequence, not a single branching decision:

1. **Pass 1** — the full invocation above.
   - Major findings → dispatch the worker once more (same Agent pattern, same
     worktree) with only those findings, then re-verify only the affected review
     group. Cap at 2 fix iterations (`review-branch.md`'s own per-invocation cap).
     Still Major after 2 fix iterations: append `- <UTC timestamp>: item N
     blocked — /review-branch still reports Major findings after 2 fix
     iterations. Worktree: <path>, branch: vps-loop/item-<N>. Findings:
     <summary>.`, no push, no PR, stop.
   - Once pass 1 is clean — whether immediately, or only after the
     fix-and-reverify cycle above — proceed to pass 2. Do not skip to Step 6
     here even though pass 1 is clean; the second pass is mandatory regardless.
2. **Pass 2** — a second, fully independent invocation: fresh `prepare_review.py`
   manifest, fresh dispatch per `review-branch.md`'s routing for this diff's tier
   (not necessarily "two reviewer groups" — follow whatever tier the diff
   actually routes to), run on the current (possibly pass-1-fixed) diff.
   - Clean → Step 6.
   - Major findings → same fix-and-reverify cycle as pass 1, capped at 2 fix
     iterations for pass 2. Once clean, proceed to Step 6 — a Major caught and
     fixed on pass 2 still satisfies the two-pass bar; do not run a third full
     pass just to reach the count. Still Major after 2 fix iterations on pass 2:
     same blocked-and-stop logging as pass 1, above.

(Doubling the mandatory full-pass count roughly doubles this step's reviewer-agent
cost for every item, including trivial ones — accepted deliberately, since a
second independent pass catching something pass 1 missed is worth more than the
extra tokens for how infrequently this coordinator runs.)

## Step 6 — Ship it

From the worktree (`git -C <worktree-abs-path> ...`). Items below are numbered
6.1–6.11; every cross-reference uses that dotted form, never a bare "step N", to
avoid confusion with this section's own "Step 6" heading.

6.1. Before pushing, record `origin/main`'s current SHA (`git rev-parse
     origin/main`) as `MAIN_SHA_AT_REVIEW` — this is the `main` that Step 5's
     passes actually reviewed against.
6.2. `git push -u origin vps-loop/item-<N>`.
6.3. `gh pr create --draft`, per `pr-github.md`'s description style (`gh pr
     create` has no `--json` output mode, so don't try to parse its stdout
     URL). Immediately follow with a structured lookup by branch name —
     `gh pr view vps-loop/item-<N> --json number -q .number` — and use that
     as `<number>` from here on, not anything parsed from create's own
     output, since this number now drives an irreversible merge below.
6.4. Replace `(PR #pending)` in `docs/refactor-log.md` with `(PR #<number>)`,
     commit on the same branch, push again.
6.5. Immediately before readying or merging, re-derive and re-verify identity:
     `gh pr view <number> --json number,headRefName,headRefOid,mergeable,
     mergeStateStatus` and assert `headRefName` equals `vps-loop/item-<N>`
     exactly (the same exact-head safeguard Step 3 already uses) — never act
     on a PR number from memory alone. Keep `headRefOid` for 6.9.
6.6. Re-fetch `origin/main` and compare its SHA to `MAIN_SHA_AT_REVIEW` (6.1).
     If `main` has advanced at all — not only if GitHub reports a textual
     conflict — treat Step 5's review as stale: merge the new `main` into the
     branch (resolving any conflicts), re-run Step 5's full two-pass review on
     the merged result, push, update `MAIN_SHA_AT_REVIEW`, and restart from
     6.5. A change reviewed only against an old `main` must not merge just
     because it happens to still apply cleanly. Cap re-syncs from 6.6/6.8 at 2
     total for this tick (mirrors Step 5's own fix-iteration cap) — if `main`
     is still advancing or GitHub's mergeability check is still not settling
     after 2 tries, stop and log `- <UTC timestamp>: item N blocked before
     merge — main kept advancing / mergeability wouldn't settle after 2
     re-sync attempts. PR #<number> left ready, not merged.` rather than
     retrying indefinitely.
6.7. `gh pr ready <number>`. Step 5 already completed both required
     `/review-branch` passes clean, and 6.6 just confirmed `main` hasn't moved
     since — mark it ready rather than leaving it in draft.
6.8. `gh pr view <number> --json mergeable,mergeStateStatus`. Only proceed to
     6.9 if `mergeable` is `MERGEABLE` and `mergeStateStatus` is `CLEAN`. Do
     not merge through a `CONFLICTING`/`DIRTY` state — if either check fails
     here despite 6.6 above, treat it the same as 6.6's "main advanced" case,
     counting against the same 2-try cap (re-sync, re-review, restart from
     6.5) rather than forcing through.
6.9. `gh pr merge <number> --squash --match-head-commit <headRefOid from 6.5>`.
     Pinning the merge to the exact head SHA closes the gap between 6.8's
     check and this call — if any commit lands on the branch in between, `gh`
     refuses instead of silently merging something unreviewed. This and 6.7
     are the exceptions to "never mark its own PR ready or merge" that used
     to apply here: both are authorized specifically because Step 5's
     two-pass gate is unconditional and 6.5/6.6/6.8 just re-confirmed nothing
     slipped in since — not a general grant to skip review or force through a
     bad state.
6.10. Run `/cleanup-merged` (this repo) to remove the now-merged local
      branch/worktree. `/cleanup-merged` is local-only by design (it never
      deletes GitHub branches — see its own file) and `gh pr merge` above
      has no `--delete-branch`, so the remote `vps-loop/item-<N>` branch is
      deliberately left behind rather than auto-deleted: this repo can have
      stacked PRs (`NEXT_TASK.md`'s "Depends on: item <M>" convention), and
      GitHub closes rather than retargets a dependent PR if its base branch
      is deleted out from under it (see CLAUDE.md's stacked-PR bullet).
      Accumulating merged-but-undeleted remote `vps-loop/item-*` branches is
      accepted operational debt, not a bug to silently fix here — a human
      can batch-delete ones with no open dependants when it's worth the time.
6.11. Append: `- <UTC timestamp>: item N merged as PR #<number>; both
      /review-branch passes clean, mergeable/clean confirmed, squash-merged and
      cleaned up.`

## Boundaries

- Never push directly to `main` — only via a reviewed, merged PR. Never force-push,
  never `git reset --hard`, never delete anything found in Step 1 (stash, don't
  discard).
- Marking a PR ready and merging it (Step 6.7/6.9) are authorized, but only after
  Step 5's two full independent `/review-branch` passes are clean, GitHub reports
  the PR mergeable/clean, AND `main` hasn't advanced since those passes ran (Step
  6.5/6.6/6.8). Never merge through a `CONFLICTING`/`DIRTY` state or a `main` that
  moved on, and never skip or shortcut the two-pass gate to reach a merge.
- If a step's tool call itself errors (a real tool/dispatch failure, not a Major
  finding), stop and log the error to the Status log with as much detail as available
  (worktree path/branch if one was created) — don't guess or retry blindly.
