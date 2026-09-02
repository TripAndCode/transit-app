---
name: review-pr
description: Read-only review of a pull request you did not author, materialized in a worktree and routed through the same prepared-diff manifest as /review-branch.
---

Review the pull request named in $ARGUMENTS. `$ARGUMENTS` is a PR number, a PR URL, or
a head branch name. This is **someone else's** PR — including one that
`/vps-loop-run` opened on a `vps-loop/item-*` branch. The output is findings, not a
cleaned-up branch.

## Boundaries (hard constraints)

- Read-only on code. Never edit, format, commit, push, or run a repository fix on the
  reviewed branch or its worktree.
- Never post to GitHub and never write a report file unless the user explicitly asks.
  Posting is `/pr-github`'s job; a written report is a separate, explicit step.
- Never leave the invoking checkout on a different branch. All revision access happens
  in the review worktree.
- Any SQL is read-only against dev Postgres and dev ClickHouse. Do not run the
  repository's test suites here — verification belongs to whoever owns the branch.

## 1. Resolve the PR

1. `gh pr view <arg> --json number,url,title,body,headRefName,headRefOid,baseRefName,author,isDraft,mergeStateStatus`,
   and resolve your own login once with `gh api user --jq .login`.
2. If `author.login` equals your own login, stop and point at `/review-branch` (own
   branch) or `/address-my-pr-comments` (own PR's threads) instead. A `/vps-loop-run`
   PR counts as someone else's work even though the account matches; say which case
   you decided and why in one line, then continue.
3. Flag a `baseRefName` other than `main`, and record `headRefOid` as the expected
   head for the rest of the run.
4. Open the report with the PR's `url` and `number`, and state up front when `isDraft`
   is true or `mergeStateStatus` is `DIRTY`/`CONFLICTING`: the first says the author
   may still be working, the second that the diff you are reading will change before
   it can merge. Both change how much weight a finding deserves; neither stops the
   review.

## 2. Materialize the head in a worktree

```bash
git fetch origin <headRefName> <baseRefName>
git worktree add .worktrees/review-<headRefName> origin/<headRefName>
```

Fetch the base branch too, not just the head. Step 3 computes the merge-base against
`origin/<baseRefName>`, and a stale remote-tracking ref there silently understates what
the PR changes relative to the base as it stands now.

`.worktrees/` is gitignored. If that path already exists it may sit on a head from an
earlier review, so fetch and then `git -C .worktrees/review-<headRefName> checkout
origin/<headRefName>` to move it forward. Before going further, confirm
`git -C .worktrees/review-<headRefName> rev-parse HEAD` equals the `headRefOid` from
step 1; a mismatch means the PR moved and every later file:line citation would be
against the wrong revision.

## 3. Prepare the diff once

Inspect `git -C .worktrees/review-<headRefName> status --porcelain` for changed **path
names only** and look for an unexpected credential-bearing file. Never print its
contents. Then, in a private scratch directory:

```bash
python3 scripts/prepare_review.py \
  --repo <review-worktree-absolute-path> --base origin/<baseRefName> \
  --output-dir <scratch-directory> > <scratch-directory>/manifest.json
```

Add `--exclude '<path-or-glob>'` to this **first** invocation for any sensitive path
found above; never build an unfiltered artifact and clean it up afterward. Run the
script from the invoking checkout — the reviewed branch may carry its own version of
it, and a PR under review does not get to choose the tool that reviews it.

Treat `manifest.json` as canonical: do not restate its file list, line counts,
exclusions, or test share in prose. State the PR's objective in one short paragraph,
from the PR title and body plus the diff, and say where the diff does more or less
than the description claims.

## 4. Route the review

Routing is `/review-branch`'s, unchanged. Read `## 2. Route the review` in
`.claude/commands/review-branch.md` and apply it as written — tiers, per-tier
dimension groups, the `comments` base-ref rule, and the high-risk overlay all live
there and are not restated here, so a change to them takes effect in both commands at
once. Use the manifest's `suggested_tier` as a suggestion and correct it when
semantics show otherwise.

Two things differ for a PR you did not author:

- The worktree path in every dispatch is the review worktree from step 2, never the
  invoking checkout.
- Findings are reported, never fixed, so the fix-triggered rerun in `/review-branch`
  §3 does not apply. One pass is the whole review; a second is warranted only when the
  PR head moves under you.

The dispatch payload, including which dimension takes a base ref, is `/review-branch`
§2's as well; the only substitution is the worktree path above.

Do not paste diff text into prompts. Reviewers never receive one another's output, and
they all read the same worktree concurrently — the agent file already forbids them
from moving it. While they run, do not move it yourself either. When they return,
re-confirm the worktree's `HEAD` against `headRefOid`; treat citations gathered
against a drifted head as suspect until re-checked.

## 5. Deduplicate against existing threads

Fetch the PR's existing review threads once and drop any candidate finding whose topic
already has a thread at the same or an equivalent file:line, regardless of who raised
it or whether it is resolved. Report in one line how many existing threads were
checked and how many candidates that removed.

## 6. Report in the terminal

Deduplicate across reviewers, keep only evidence-backed findings that affect
correctness, security, performance, enforcement, or the stated objective, and drop
low-confidence noise. Rank `Major` before `Minor`. Each finding carries confidence,
file and line inside the review worktree, impact, and a concrete fix. List obstacles
separately. If nothing qualifies, say so plainly.

Check that new logic has a concrete matching test, and use the manifest's `test_share`
only as a prompt to inspect, never as a finding on its own. A missing test is a
finding here, not something you fix.

## 7. Offer teardown

After the user has seen the findings, offer
`git worktree remove .worktrees/review-<headRefName>`. Leaving it in place is fine and
lets `/follow-up-pr-review` diff the next push against the head reviewed here — say
which you did, since that command needs the baseline.
