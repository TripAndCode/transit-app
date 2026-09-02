---
name: follow-up-pr-review
description: Follow up on your own review threads on a PR you did not author, and scan whatever commits landed since your last look for regressions, staleness, and refactor opportunities. Settled threads are reported only; open threads and new findings get a drafted reply behind a per-item approval gate. Never resolves a thread.
---

Follow up on the threads **you** opened on a PR **you did not author**, after the
author replied or pushed a fix, and scan whatever commits landed since your last look.
Two jobs in one run: judge whether each reply actually settles your point, and review
the delta rather than the whole PR again.

`$ARGUMENTS` is a PR number, URL, or head branch name — required, since this is not
your branch.

The natural predecessor is `/review-pr`, which leaves a worktree at
`.worktrees/review-<headRefName>`. That worktree's current head **is** the baseline
for the delta scan. If it does not exist, there is no baseline and the delta scan is
skipped — do not silently fall back to diffing against `main`, which is `/review-pr`'s
job, not this command's.

## Hold for approval (hard constraint)

Nothing is posted to GitHub without an explicit per-item go-ahead in Phase 3.
Presenting a draft table is not approval. Approving one item does not approve any
other. Silence, or moving to a different topic, is not approval — when it is unclear
whether an item was approved, treat it as not approved and ask again. This command
never resolves a review thread; see Boundaries.

## Skills (invoke when relevant)

- `superpowers:receiving-code-review` — before judging any reply, verify it against
  the actual code instead of accepting performatively or dismissing blindly.
- `transit-app-gotchas` / `postgres-perf` / `maplibre-map` — when a thread or a new
  commit touches the area they cover.

## Auth

Use the `gh` CLI, already authenticated via keyring. Never ask for, paste, or store an
API token. If a call fails, check `gh auth status`.

## Phase 0 — Materialize the head and capture the baseline

1. `gh pr view <arg> --json number,url,title,body,headRefName,headRefOid,author`, and
   resolve your own login once with `gh api user --jq .login`. Open the report with the
   PR's `url` and `number`, as `/review-pr` does.
2. **Before touching the worktree**, capture its current head as `old_head`:
   `git -C .worktrees/review-<headRefName> rev-parse HEAD`. An error or empty result
   means no baseline — record that case for Phase 1.
3. State the PR's objective in one short paragraph, from the `title` and `body` just
   fetched. Phase 1's dispatch needs it.
4. Fetch and move the worktree to the PR's current head:
   `git fetch origin <headRefName>`, then
   `git -C .worktrees/review-<headRefName> checkout origin/<headRefName>`, creating the
   worktree with `git worktree add .worktrees/review-<headRefName>
   origin/<headRefName>` if it is absent. Record the result as `new_head` and confirm
   it equals `headRefOid`.

## Phase 1 — Scan the delta for regressions, staleness, and refactor opportunities

Skip this phase entirely when `old_head` is missing (say "first run on this worktree,
no baseline, delta scan skipped") or when `old_head == new_head` ("no new commits since
the last look").

Also confirm `git -C <review-worktree> merge-base --is-ancestor <old_head> <new_head>`
succeeds. When it fails the branch was rebased or force-pushed, so `old_head` is no
longer a baseline: `prepare_review.py` would fall back to an older common ancestor and
re-scan commits the previous run already covered. Say the branch was rewritten and skip
the scan, pointing at `/review-pr` for a full re-review instead.

Otherwise prepare the delta the same deterministic way the rest of the repo does, from
a private scratch directory:

```bash
python3 scripts/prepare_review.py \
  --repo <review-worktree-absolute-path> --base <old_head> \
  --output-dir <scratch-directory> > <scratch-directory>/delta-manifest.json
```

Run the script from the invoking checkout, not the reviewed branch's copy of it.
Inspect the delta's changed **path names only** for an unexpected credential-bearing
file first and pass `--exclude '<path-or-glob>'` on this **first** invocation if one
turns up; never build an unfiltered artifact and clean it up afterward.

Dispatch **one** `branch-reviewer` with a custom brief covering exactly three things,
scoped to this delta only:

- **Regressions** — bugs or logic errors the new commits introduce.
- **Staleness** — comments, docstrings, tests, locale entries, or docs that the new
  commits leave contradicting the code, including anything the same commits
  refactored away. Narrow this half first by running
  `python3 "$HOME/.claude/scripts/comment_lint.py" --root <worktree> --stale-candidates <merge_base>`
  and judging only what it lists. That script is personal tooling under `$HOME`, not
  part of the repository. If it fails or returns an empty list — it reads Python and
  TypeScript sources only — fall back to the comments and prose beside the delta's
  changed lines.
- **Refactor opportunities** — duplication or unnecessary ceremony the new commits
  introduce or leave behind, with a concrete simpler alternative.

That dispatch receives only: the delta manifest path, the delta diff path, the PR
objective, the brief above, the review worktree path, the delta manifest's
`merge_base` — the brief's staleness bullet names it — and this exact line:

`Deliberately excluded, do NOT re-derive: <manifest paths>. This is not truncation.`

A delta is usually small, so one call is the right size; escalate to the full
`/review-pr` routing only if the new commits are large enough to be a PR of their own,
and say so rather than quietly fanning out.

## Phase 2 — Fetch, filter, and judge your threads

1. `gh api repos/{owner}/{repo}/pulls/<number>/comments --paginate`. Group into
   threads via `in_reply_to_id` (the root has none; replies carry the root's `id`),
   recording each comment's `user.login`. Compute
   `round = comment_count_in_thread - 1`.
2. Keep only threads whose **root comment's `user.login` is your own** and where at
   least one later comment carries a **different** `user.login`. A reply count alone
   does not prove anyone answered you: a follow-up remark you added to your own thread
   while waiting would satisfy `comment_count > 1` and be misread as a reply.
   When the PR's `author.login` is your own login — a `/vps-loop-run` PR is the usual
   case — no thread can satisfy this, which is correct rather than a bug: there
   is no human on the other side to have answered. Say so, and let Phase 1's delta
   scan carry the run.
3. Resolution state does not exist on the REST comments endpoint. Fetch it from
   GraphQL and drop resolved threads:
   ```bash
   gh api graphql -f query='
     query($owner:String!,$repo:String!,$pr:Int!) {
       repository(owner:$owner, name:$repo) {
         pullRequest(number:$pr) {
           reviewThreads(first:100) {
             nodes { id isResolved isOutdated comments(first:50) { nodes { databaseId } } }
           }
         }
       }
     }' -f owner=<owner> -f repo=<repo> -F pr=<number>
   ```
   Match each REST comment `id` against a thread's `comments.nodes[].databaseId` for
   its `isResolved` flag. The query is unpaginated (100 threads, 50 comments each), so
   on a larger PR page through `pageInfo` before trusting a miss. `isOutdated` means
   the anchored line has since changed and GitHub collapses the thread by default — a
   thread you cannot find in the diff view is usually collapsed, not deleted.
4. If nothing survives the filter and Phase 1 produced no findings, say so and stop.
5. For each surviving thread, read the code at the referenced file:line in the review
   worktree as it stands now — not just the reply text — and read the whole exchange.
   Targeted read first (`grep -n` for the symbol, then `sed -n '<start>,<end>p'` for a
   window), whole-file only when that is not enough. At roughly ten threads or more,
   group them by file/topic and dispatch one `Explore` subagent per batch so threads
   making the same point stay together; below that, judge them yourself. Any batch
   prompt must carry, verbatim: "You are read-only: do not edit any file, do not run
   any `gh` write call, do not post or reply to any comment, never call the resolve
   mutation, do not commit or push. Any SQL is read-only SELECT/EXPLAIN against dev
   Postgres :5433 or the dev ClickHouse (`transit-ch`) — never write to either. Read
   only within the worktree path given; never read another worktree. Report only."

Produce a numbered table, one row per thread:

- **Your original point** — restate what you asked for.
- **What they did** — their reply and the relevant code change.
- **Verdict** — does the current code actually address the point? Cite file:line.
- **Classification** — `settled` (the exchange is genuinely over; **no action**, do
  not draft a reply or route it through Phase 3, and resolve it yourself in the GitHub
  UI if you want it closed) or `discuss` (the fix is partial, misread the point, or
  the reasoning does not hold). When in doubt, `discuss`.
- **Round flag** — for `discuss` threads with `round >= 2`, mark `stuck`, re-examine
  from scratch instead of extending the same argument a third time, and consider
  recommending a synchronous discussion in the draft.

**Combined report.** Show one report and stop: Phase 2's table split into **Settled
(no action)** and **Discuss (needs a reply)**, then Phase 1's findings as
observation plus draft reply, or its skip reason. Draft no further text and post
nothing until the user has seen this.

## Phase 3 — Per-item decision gate

`settled` threads are already fully reported and need nothing further. For each
`discuss` thread, present the **actual draft reply text** alongside its `stuck` flag.
For each Phase 1 finding, present its draft the same way, with no `stuck` flag since
these are first contact. Per item, the user may keep it, edit the wording, reclassify
or dismiss it, or skip it for now.

Wait for an explicit decision on **every** item individually. Nothing is posted in this
phase, and no decision carries over to another item.

## Phase 4 — Execute only what was approved

1. Approved thread replies:
   `gh api repos/{owner}/{repo}/pulls/<number>/comments -f body="<reply>" -F in_reply_to=<root_comment_id>`.
2. Approved Phase 1 findings are new threads, not replies. Anchor to the current head:
   `gh api repos/{owner}/{repo}/pulls/<number>/comments -f body="<reply>" -f commit_id=<headRefOid> -f path=<path> -F line=<line> -f side=RIGHT`.
   Post a finding with no single file:line as a plain PR comment via
   `gh pr comment <number> --body "<reply>"`.
3. Never call the `resolveReviewThread` mutation, for any thread. Even for a thread you
   opened, this command stops at replying.
4. Report each posted reply and comment with its URL.

## Reply-style rule (hard constraint)

Plain English: short, concrete, addressing the author directly ("you", "I"), stating
what you think and why in one or two sentences, with no unexplained jargon. For a
dismissal, give the actual reason rather than a brush-off.

## Boundaries

- Read-only on code, on both the branch and its worktree. Never edit, format, commit,
  or push — this is someone else's branch, and a fix is theirs to make.
- `settled` threads get zero action: no reply, no post, no resolve, no approval prompt.
- Post nothing before Phase 4, Phase 1 findings included.
- **Never resolve a thread**, settled or not. Resolving is a manual step in the GitHub
  UI.
- Phase 1 never invents a baseline. Without a prior `/review-pr` worktree, the delta
  scan is skipped and reported as skipped.
- Any SQL is read-only against dev Postgres and dev ClickHouse. Do not run the
  repository's test suites on someone else's branch.
