---
name: address-my-pr-comments
description: Digest and triage unresolved GitHub review-comment threads on your own PR (current branch) — fresh comments with no reply yet, and threads that already got a reply or fix. Judges each against the current code, classifies it (settled / valid / partly / disagree / need-info), applies approved code fixes, and posts approved replies. Never resolves threads itself — that's the reviewer's call.
---

Address the review comment threads on the GitHub PR for the current branch — both
comments with no reply yet, and threads that already have a reply or fix but are
still unresolved. The goal is to **think before acting**: judge each thread against
the real code, propose a reply and an action, and WAIT for the user's per-thread
approval before posting anything or changing any code.

`$ARGUMENTS` (optional) is a PR url or number. If omitted, resolve the PR from the
current branch.

## Hold for approval (hard constraint)
Nothing gets posted to GitHub, and no code gets changed, without your explicit
per-thread go-ahead in Phase 2. Presenting a draft table is not approval. Approving
one thread does not approve any other thread. Silence, or moving on to a different
topic, is not approval — if it's unclear whether a thread was approved, treat it as
not approved and ask again. This command never resolves review threads — see
Boundaries.

## Skills (invoke when relevant)
- `superpowers:receiving-code-review` — invoke before judging any thread; verify the
  reviewer's point against the actual code, don't agree performatively or dismiss
  blindly.
- `superpowers:systematic-debugging` — when a thread reports a bug or a change
  breaks a test, before proposing a fix.
- `transit-app-gotchas` / `postgres-perf` / `maplibre-map` — invoke when a thread
  touches the area they cover.

## Auth
- Use the `gh` CLI — already authenticated via keyring. Do NOT ask for, paste, or
  store any API token. If a call fails, check `gh auth status`.

## Phase 0 — Fetch & filter threads (you, directly)
1. Resolve the PR for the current branch (or `$ARGUMENTS`):
   `gh pr view --json number,url,headRefName,headRefOid,baseRefName`.
   If none exists, stop and report — do not create one.
2. Pull all inline review comments:
   `gh api repos/{owner}/{repo}/pulls/<number>/comments --paginate`.
   Group into threads via `in_reply_to_id` (root comment has none; replies point at
   the root's `id`). Compute `round = comment_count_in_thread - 1`.
3. Filter to KEEP threads where at least one comment is NOT authored by you.

   **Known API quirk — the REST comments endpoint has no "resolved" field at all.**
   Thread-resolution state lives only on the GraphQL `reviewThread` object, not on
   individual REST review comments. Fetch it separately:
   ```bash
   gh api graphql -f query='
     query($owner:String!,$repo:String!,$pr:Int!) {
       repository(owner:$owner, name:$repo) {
         pullRequest(number:$pr) {
           reviewThreads(first:100) {
             nodes { id isResolved comments(first:50) { nodes { databaseId } } }
           }
         }
       }
     }' -f owner=<owner> -f repo=<repo> -F pr=<number>
   ```
   Match each REST comment's `id` against a thread's `comments.nodes[].databaseId`
   to find its `isResolved` flag. DROP threads where `isResolved` is true — do not
   trust anything off the plain REST list for resolution state.
   **Limit:** this query is unpaginated (`first:100` threads, `first:50` comments
   per thread) — on a PR with more threads/comments than that, a match can be
   missed and an already-resolved thread could resurface. Fine for this repo's PR
   sizes; if a PR ever gets that large, page through `pageInfo` before trusting a
   miss.
4. If nothing is left after filtering, say so and stop.

## Phase 1 — Digest & analyze (NO replies written, NO code touched)
**Scale how you do this to the thread count.** Under ~10 threads, judge them yourself
directly. At ~10 or more, dispatch fresh-context subagents instead of reading
everything yourself — but **group threads by file/topic FIRST, then split those groups
into batches** (bounded so each subagent handles a set that fits comfortably in one
context, ~10 threads), so threads making the same point stay in one batch.
Each batch prompt must carry, verbatim: "You are read-only: do not edit any file, do
not run any `gh` write call, do not post or reply to any comment, never call the
resolve mutation, do not commit or push. Any SQL is read-only SELECT/EXPLAIN against
:5433. Report only." — a dispatched subagent doesn't see this command file, so the
per-thread approval gate above binds it only if you say so.
Give each batch: its thread text, the worktree path, the verdict taxonomy and row
fields below, and the targeted-read rule. Each batch returns those same row fields,
one row per thread. After all batches return, run one cross-batch pass yourself to
merge rows whose underlying point is the same before emitting the table.

For EACH thread, read the actual code at the referenced file:line before judging —
targeted read first (`grep -n` for the symbol, then `sed -n '<start>,<end>p'` for a
window around it), whole-file read only when that isn't enough — and read the full
exchange if `round >= 1` (original comment + all replies/fixes so far). If two or
more threads make the same underlying point, don't judge and draft each in isolation:
note the overlap and produce one shared verdict/reply covering all of them.
Produce a numbered table, one row per thread:
- **What they mean** — restate the reviewer's point in plain words (decode any
  shorthand).
- **What happened since** — only if `round >= 1`: summarize the reply/fix chain so
  far.
- **My read**, grounded in the code you just read (cite file:line), one of:
  - `settled` — the point is already fully addressed by the current code (whether
    that code existed already or a prior fix landed) and nothing more needs saying.
    **No action**: don't draft a reply, don't touch code, don't route it through
    Phase 2. Report it as settled and move on — the reviewer resolves it themselves
    whenever they get to it.
  - `valid` — the point is correct and needs a code change.
  - `partly` — partially correct; some of it needs a change, some doesn't.
  - `disagree` — the point doesn't hold up against the code as it stands.
  - `need-info` — can't judge without more context from the reviewer.
- **Candidate action** (skip for `settled`) — `reply-only` / `change-then-reply` /
  `dismiss-with-reason` / `ask-clarification`.
- **Round flag** — if `round >= 2` (already gone back and forth at least twice
  without resolving), mark it `stuck`. Re-examine `stuck` threads from scratch
  before drafting anything — don't just extend the same argument a third time.
  Consider recommending a synchronous discussion instead of another async reply,
  and say so in the draft.

This is the digest stage. STOP here and show the table, split into a **Settled (no
action)** list and a **Needs action** list. Do not draft replies or touch code until
the user has seen it.

## Phase 2 — Per-thread decision gate (needs-action threads only)
`settled` threads need nothing further — they were already fully reported in Phase 1.
For each remaining thread, present the **draft plain-English reply** (actual wording,
not a label) alongside its candidate action, so the user approves the real text. Let
the user decide per thread:
- keep the proposed action + reply,
- edit the wording,
- change the disposition (e.g. `reply-only` → `change-then-reply`),
- reclassify as `settled` if the user disagrees and considers it closed,
- or skip it.

WAIT for an explicit decision on EVERY thread individually. Nothing is posted and no
code is changed in this phase, and nothing carries over from a decision on a
different thread.

## Phase 3 — Execute (only the items explicitly approved in Phase 2)
1. **Apply all approved code changes as one batch** (all `change-then-reply` items
   together). DB SAFETY: if a fix touches DB code, tests point at the throwaway DB —
   `DATABASE_URL=postgresql://transit:transit@localhost:5544/transit_test`. NEVER
   let a run hit dev DB :5433. See CLAUDE.md / transit-app-gotchas.
2. **Run `/review-branch`** on the result — fresh-context subagent review + iterative
   fix. This pass is mandatory whenever any code changed; do not skip it. "Green"
   means: no findings ranked Major or higher remain, and any Minor findings are
   either fixed or explicitly acknowledged to the user. Cap at 2 review passes —
   if Major findings still remain after that, stop and report the residual
   findings to the user instead of continuing to iterate.
3. Show the diff plus the `/review-branch` evidence (findings handled, `make check`
   output). **Ask before any commit or push** — do not commit or push on your own.
   If the user defers or declines the push, hold the approved `change-then-reply`
   replies (don't post them, don't drop them) and re-offer to post them once a
   push actually lands.
4. Post the approved replies as inline **thread replies**:
   `gh api repos/{owner}/{repo}/pulls/<number>/comments -f body="<reply>" -F in_reply_to=<comment_id>`.
   Post `change-then-reply` replies only after the change is pushed; post
   `reply-only` / `dismiss` replies right away. Report each posted reply with its
   URL.
5. Never call the `resolveReviewThread` GraphQL mutation, for any thread. Resolving
   is the reviewer's call, not yours — report posted replies and settled threads,
   and leave resolving to them (GitHub UI: "Resolve conversation").

## Reply-style rule (hard constraint)
Replies MUST be plain English:
- Short and concrete; address the reviewer directly ("you", "I").
- State what you did and why — or, if you disagree, why — in one or two sentences.
- No unexplained jargon, acronyms, or cryptic shorthand. If a term is unavoidable,
  define it in the same breath.
- For a change: name what changed and where (e.g. "Moved the null check above the
  loop in `api/routers/agencies.py:142`"). For a dismissal: give the actual reason,
  not a brush-off.

## Boundaries
- `settled` threads get zero action — no reply, no code change, no resolve, no
  approval prompt. Reporting them is the only output.
- Read-only until the user approves each item in Phase 2. No code edits, no posted
  comments before that.
- Do NOT commit or push without an explicit go from the user (per CLAUDE.md).
- If work is in a git worktree, run git via `git -C <worktree-abs-path>` and confirm
  commits land on the feature branch, not `main`.
- **Never call the resolve mutation, on any thread.** Resolving your own PR's
  threads is the reviewer's call, not the author's.
- Skip already-resolved threads and don't reopen or re-litigate settled points
  unless the user asks.
