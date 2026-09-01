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

## Step 0 — Circuit breaker: back off after a repeated identical blocker

Every Status log entry logged when a tick stops making zero forward progress
because of a genuine blocker — Step 1's stash-and-stop, Step 2's
tool-error stop, Step 2b's branch-only-no-worktree stop, Step 3b's
worktree-dirty/branch-without-worktree/still-Major-after-2-fix-iterations
stop paths, Step 4b's
worker-couldn't-complete, Step 5/6's blocked-after-fix-iteration-cap stops,
and Boundaries' generic tool-call-errored stop — must end with its own
line: `**Blocker-tag:** <kebab-case-slug>`. Pick the slug to name the root
cause's *class* (e.g. `review-scratch-leftover`, `git-stash-permission-denied`,
`settings-drift`, `sensitive-file-no-approver`, `db-write-blocked`), not the
specific instance (not the item number, not the exact file path) — the same
class of problem recurring on different items must reuse the identical slug,
or this mechanism can never detect the pattern. Two outcome-category slugs
(`review-major-unresolved` for an unresolved review finding,
`worker-blocked` for a Step 4b report) are generic buckets, not
necessarily a real recurring root cause on their own — before reusing one of
these because the last 2 entries also used it, sanity-check that the
underlying cause is actually the same, not just the same outcome shape; if
it's clearly a different underlying issue that happens to also end in an
unresolved review or a blocked worker, use a more specific compound slug
instead (e.g. `review-major-unresolved-null-handling`) so unrelated one-off
failures don't spuriously trip the streak. This tagging requirement does NOT
apply to a "skip item N, keep going" outcome that doesn't stop the whole
tick (e.g. Step 3b's closed-PR skip — no `Blocker-tag` there, it's an item
skip, not a tick stop; see Step 0 — or Step 3's ordinary "item still open,
try another") — only to an outcome where the tick stops entirely with no
other progress.

One entry = the text from one leading `- <UTC timestamp>: ...` bullet up to
(excluding) the next line that starts with `- <UTC timestamp>`, regardless
of any `-`-prefixed lines embedded within that entry's own prose (e.g. a
findings summary) — don't miscount those as separate entries. `**PAUSED
...**`/`**Still paused ...**`/`**RESUMED ...**` bookkeeping lines
(introduced below) are their own entries too, in the same `- <UTC
timestamp>: **PAUSED ...**` bulleted form as everything else in this log —
never a bare unbulleted line — but they are bookkeeping markers, not
`Blocker-tag`-bearing occurrences; keep them out of the tag-streak count
described next (none of the three extend or restart the streak that
triggered them, and none is ever itself one of the 3 entries counted
toward it).

**"3 in a row" means the last 3 tag-bearing Status-log entries share an
identical tag, not 3 literally-adjacent Status-log entries.** An unrelated
entry with NO `Blocker-tag` at all (an idle-throttle line, an ordinary
shipped item) sitting between two occurrences of the same tag does NOT
break the count or consume one of the 3 slots — the same root cause
recurring with an occasional unrelated success interleaved is still the
same recurring problem worth escalating, arguably more so than requiring
zero interruption. A genuinely DIFFERENT blocker (its own, different
`Blocker-tag`) occurring in between DOES break the count: it occupies one
of the 3 most-recent tag-bearing slots with a non-matching tag, and is
itself real evidence the situation changed, not something to silently
look past — only no-tag interleaving is transparent to this count, not
every interleaving. Worked example (oldest → newest,
Blocker-tag lines omitted from this summary for brevity, each really has
its own `**Blocker-tag:** db-write-blocked` line):
```
10:00 item 21 blocked before verification — db write refused.
10:20 item 22 blocked before verification — db write refused.
10:40 item 23 shipped. PR #501 merged.
11:00 item 24 blocked before verification — db write refused.
```
At the 11:20 tick, the last 3 `db-write-blocked` occurrences are 10:00,
10:20, and 11:00 — the 10:40 shipped entry doesn't count and doesn't
interrupt them. This is a real streak: log `**PAUSED after the last 3 ticks
blocked on db-write-blocked. Backing off ...**`, exactly matching the
template given below — note it says "the last 3 ticks," not "3 consecutive
ticks," precisely to avoid implying strict Status-log adjacency.

At the very start of every tick, before Step 1, determine pause state in
two steps:

1. **Am I currently paused?** Scan the Status log backward for the most
   recent pause-related bookkeeping entry: a `**PAUSED ...**` line, a
   `**Still paused ...**` confirming-probe line (introduced below), or a
   `**RESUMED ...**` line — "PAUSED-family" below means either of the
   first two. If none exists yet, or the most recent one is `RESUMED`, you
   are NOT currently paused — skip to step 2. If the most recent one is
   PAUSED-family, you ARE currently paused — do not re-derive this from
   the raw tag streak each tick, this marker is authoritative:
   - Still run Step 1 every tick as normal even while paused (it's cheap,
     and it's this repo's only guard against an unrelated new problem like
     a leaked credential file landing at the top level while paused for a
     different reason — never skip it). If Step 1 itself finds new
     unexpected state, handle that normally (stash-and-log per Step 1,
     with its own `Blocker-tag`) regardless of the existing pause.
   - Beyond Step 1, do NOT run the rest of the normal flow every tick —
     only attempt it once enough wall-clock time has passed since the most
     recent PAUSED-family entry's own UTC timestamp: roughly three times
     the actual interval the invoking cron job runs on, read directly from
     the live crontab (`crontab -l`) rather than any hardcoded or
     documented figure — this repo has more than one stale,
     mutually-conflicting cadence number on record across different files,
     so the crontab itself is the only source guaranteed current. Compute
     this from each entry's own timestamp, not by counting
     ticks — a tick where not enough time has passed yet logs nothing at
     all (see below), so it's indistinguishable from any other silent tick
     except by timestamp math; a tick counter cannot be reliably
     reconstructed from the log, but elapsed wall-clock time can. On a
     tick where not enough time has passed yet, stop here with no new log
     line at all, not even a repeat of the pause.
     There is no separate "probe check" to invent per tag: a probe attempt
     IS a normal, full, unrestricted run of Steps 2 onward, exactly as any
     tick would otherwise do — the same logic that originally produced the
     stop is what re-confirms or clears it, because it's the same code
     path. Two outcomes:
     - **The attempt reaches a genuinely different outcome than the paused
       tag** (progress is made — an item ships, a different item is
       picked, or the same item now proceeds past where it previously
       stopped — or the tick ends idle/clean with no matching
       `Blocker-tag` at all): log `**RESUMED after N paused ticks — <tag>
       cleared.**` FIRST, then let that attempt's own normal outcome
       logging (per whichever of Steps 2-6 it actually reached) proceed as
       its own separate, later entry — including a fresh `**Blocker-tag:**`
       line if the attempt itself stops on a new, different blocker.
       Logging `RESUMED` first, not merely "in addition to" with
       unspecified order, matters: it guarantees that if this new blocker
       recurs later, its first occurrence here is never wrongly excluded
       by step 2's own "do not scan past the most recent `RESUMED`" rule
       below, since that boundary now sits chronologically before, not
       after, this occurrence. This `RESUMED` entry is what step 1 above
       will see on the next tick, correctly reporting "not currently
       paused" from then on.
     - **The attempt stops again with the identical `Blocker-tag`:** this
       is a confirming probe, not a new occurrence to react to. Log
       `**Still paused — probe found <tag> unchanged.**` as its own
       timestamped entry — a PAUSED-family bookkeeping marker exactly like
       the original `PAUSED` line, not a `Blocker-tag`-bearing occurrence
       (see the definition above). This entry's timestamp is what step 1
       measures elapsed time against for the *next* probe; skipping this
       log line would make that elapsed-time calculation impossible from
       the log alone. It also means a human checking in after a long pause
       finds one line per probe cycle rather than a wall of total silence,
       so no separate long-stretch reminder mechanism is needed.
2. **Not currently paused — should I newly enter pause this tick?** Read
   backward through the Status log, but do NOT scan past the boundary step
   1 already found: stop at (do not look behind) the most recent
   `RESUMED` marker, or the top of the log if none exists yet. (By
   construction this is always the correct boundary here: if a
   `PAUSED`/`Still paused` entry more recent than that existed with no
   later `RESUMED`, step 1 above would have reported "currently paused"
   and step 2 would never run at all this tick.) A `RESUMED` genuinely
   resets this streak's window, so a tag-bearing entry from before it must
   never count again, even if it's still among the numerically-nearest
   entries. Within that bounded window, find the most
   recent 3 entries that themselves carry a `Blocker-tag` (skipping over
   Step 3's idle-throttle lines and ordinary item-shipped entries, which
   don't carry one — an unrelated successful tick in between does not
   reset or interrupt this count, per the worked example above). If those
   3 share an identical tag, log `**PAUSED after the last 3 ticks blocked
   on <tag>. Backing off to a reduced probe cadence until this clears or a
   human resolves it.**` and stop — skip Steps 1 through 6 entirely this
   tick. If fewer than 3 tag-bearing entries exist within the bounded
   window (including zero, e.g. right after a fresh `RESUMED`), that is
   not a streak — proceed to Step 1 as normal; this is an ordinary,
   unrestricted tick.

This must not interfere with Step 3's own "nothing actionable this run"
idle-throttling convention for an empty/fully-claimed backlog — that's a
different, benign kind of "no progress" and must NOT accumulate toward this
circuit breaker's streak count. Only genuine blocker/failure outcomes count.

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
   End the entry with a `**Blocker-tag:**` line per Step 0 — name the file
   shape's class (e.g. `review-scratch-leftover` for a leftover review
   scratch dir/file, or a new slug if the shape doesn't match one already
   in use).
4. Stop. No later step runs this tick.

## Step 2 — Sync main and clear proven-stale state

1. `git checkout main && git pull --ff-only`.
2. Run `python3 scripts/cleanup_git_state.py` and inspect its complete plan, then run
   `python3 scripts/cleanup_git_state.py --apply`. This is the canonical cleanup
   policy shared with `/cleanup-merged`: it removes only clean local worktrees/refs
   that are already recoverable from `main` or an exact merged-PR head. It retains
   open PRs, dirty/locked worktrees, unique commits, `production`, and remote branches.
3. If either command errors, follow the Boundaries tool-error rule (including its
   `**Blocker-tag:**` requirement per Step 0): log it and stop this tick. Never
   replace a retained decision with manual force deletion.

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
commits)"** — not any other KEEP reason — check whether that tip actually
carries unshipped substance. This KEEP reason fires for a bare local branch
with no attached worktree just as often as for one with a worktree (the
script doesn't distinguish), so establish which shape you have first — `git
worktree list` — before choosing an action below; the two shapes are not
interchangeable, the same way Step 3b already forks on this.

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
   itself. This step requires reading both sides, not a scripted check — and
   unlike `cleanup_git_state.py`'s own mechanical, exact-match deletion
   criteria (ancestor-of-base, tree-matches-base, or exact merged-head OID),
   this judgment can be wrong. Treat it accordingly in step 3 below.
3. **Content already covered on `main` (the common case for an old, stale
   worktree):** before removing anything, create a cheap, reversible escape
   hatch in case this judgment call turns out to be wrong —
   `git branch vps-loop/item-<N>-superseded-<short-sha>
   vps-loop/item-<N>` (a backup ref, not a stash: this may be a bare branch
   with no worktree to stash from). Then:
   - *Worktree exists:* `git worktree remove --force <path>`, then
     `git branch -D vps-loop/item-<N>`.
   - *Branch only, no worktree:* `git branch -D vps-loop/item-<N>` (no
     worktree step needed).
   Append `- <UTC timestamp>: item N's leftover vps-loop/item-<N>
   worktree/branch is fully superseded — every change it made is already
   present on main via later, unrelated work. Backed up as
   vps-loop/item-<N>-superseded-<short-sha> before removing the original.`
   Do not leave the original for a future tick to re-discover;
   `cleanup_git_state.py` will not reclaim it on its own since the tip
   genuinely differs from any merged head by its literal commit hash. Leave
   the backup ref for a human to eventually prune — it's not `cleanup_git_state.py`-managed and won't be auto-deleted.
4. **Genuinely new, unshipped content confirmed:** item N is NOT done despite
   its merged PR.
   - *Worktree exists:* append `- <UTC timestamp>: item N's merged PR
     (#<original number>) doesn't cover all commits on vps-loop/item-<N>;
     unshipped content found (<short description>). Routing to Step 3b's
     resume-and-ship path as a follow-up this tick.` and go straight to Step
     3b's "real commits exist, worktree exists" branch for item N.
   - *Branch only, no worktree:* this needs a worktree to safely review and
     ship from (same reason Step 3b's own "Branch only, no worktree" case
     refuses to proceed) — append `- <UTC timestamp>: item N's merged PR
     (#<original number>) doesn't cover all commits on vps-loop/item-<N>
     (branch only, no worktree); unshipped content found (<short
     description>). Not resumed — needs a human to attach a worktree or
     delete the branch.
     **Blocker-tag:** branch-without-worktree` and do not act on it further
     this tick — reusing Step 3b's exact tag for this same root cause, not
     a fresh compound slug, since it's genuinely the identical stop reason.
   Only route the FIRST such match to Step 3b in a given tick — this step
   still respects the one-item-per-tick design the rest of this file assumes.
   If a second or later `vps-loop/item-<M>` entry also lands in this branch
   during the same evaluation pass, give it its own status-log line (`- <UTC
   timestamp>: item M also has unshipped content behind its merged PR;
   deferred to a future tick.`) rather than acting on it or silently dropping
   it — it keeps its normal queue position and gets picked up on a later
   tick's own Step 2b pass.

Skip this step entirely on a tick where Step 2 retained no worktree/branch for
this reason — it only exists to catch the one blind spot described above, not
to re-audit every retained worktree on every tick.

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
delete the branch or reopen the PR by hand.` and skip item N this tick. No
`**Blocker-tag:**` line here — this is an item skip, not a tick stop (see Step 0);
the tick itself may still continue on to a different item.

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
    leftover"`, log the stash ref, worktree path, file list, and a `**Blocker-tag:**`
    line per Step 0 (e.g. `worktree-dirty-pre-commit`), and stop this tick.
    Boundaries say stash, don't discard — and Step 1's check never looks inside a
    worktree.
  - Clean: `git worktree unlock <path>` (ignore "not locked"), `git worktree remove
    --force <path>`, `git branch -D vps-loop/item-<N>` if still present, then Step 4.

**Log non-empty — real commits exist** and no closed PR rejected them:
- *Branch only, no worktree:* do NOT review from the main checkout — Step 2 just left it
  on `main`, so `git diff main...HEAD` there is empty and would produce a vacuously
  clean review of unreviewed commits. Log `- <UTC timestamp>: item N has an
  un-reviewed leftover branch with commits but no worktree: <commit list>. Not resumed
  — needs a human to attach a worktree or delete it.
  **Blocker-tag:** branch-without-worktree` and stop this tick.
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
     + worktree path to the Status log, plus a `**Blocker-tag:**` line per Step 0
     (e.g. `review-major-unresolved`), no push, no PR, stop.

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
<its blocker, summarized>. Worktree: <path>, branch: vps-loop/item-<N>.`, plus a
`**Blocker-tag:**` line per Step 0 naming the blocker's class (e.g.
`sensitive-file-no-approver`, `db-write-blocked` — reuse an existing slug if this
matches a cause already seen, don't invent a near-duplicate; fall back to Step 0's
generic `worker-blocked` bucket only if the report doesn't fit any more specific,
already-established class yet), no push, no PR, stop.

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
     <summary>.
     **Blocker-tag:** review-major-unresolved`, no push, no PR, stop.
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
     same blocked-and-stop logging as pass 1, above (including the
     `**Blocker-tag:** review-major-unresolved` line).

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
     re-sync attempts. PR #<number> left ready, not merged.
     **Blocker-tag:** merge-resync-unsettled` rather than retrying indefinitely.
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
  (worktree path/branch if one was created) — don't guess or retry blindly. This is
  a genuine tick-stopping blocker: end the entry with a `**Blocker-tag:**` line per
  Step 0 (e.g. a denied `git`/Bash call gets a slug like
  `git-stash-permission-denied` or `sensitive-file-no-approver` depending on what
  was actually denied — reuse the existing slug if the same command shape was
  already denied before, rather than inventing a near-duplicate).
