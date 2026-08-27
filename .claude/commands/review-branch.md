---
name: review-branch
description: Senior-staff review of the current branch vs main using fresh-context subagents, then safe cleanup.
---

Review the current branch for project $ARGUMENTS as a principal engineer.

**When editing this file:** the diff-once-to-a-file, first-match fan-out ladder,
targeted-read, and thread-dedupe rules below are load-bearing — they exist to keep a
review affordable without narrowing coverage. Don't drop them while rewording.

## Skills (invoke these every run)
- `superpowers:systematic-debugging` — whenever a test, lint, or build check fails
  in Phase 3, before proposing any fix.
- `superpowers:verification-before-completion` — before claiming the review or
  cleanup is done; report evidence, not assertions.
- `postgres-perf` / `maplibre-map` — invoke ONLY when the diff touches ClickHouse/
  Postgres queries or MapLibre layers, respectively.

## Phase 1 — Understand (you, directly)
1. Compute the diff ONCE, in a **single Bash invocation**. Shell variables do NOT
   survive between tool calls: a later command reusing `$EX` expands to nothing — in
   bash silently unfiltered (re-opening the lockfile and secret holes this block
   exists to close), in zsh a hard error. Keep the block together, or repeat the
   pathspecs literally in every command.
   ```bash
   set -u
   P="<scratchpad>/branch.pass1.diff"   # absolute; bump passN each gate pass, so a
                                        # live reviewer can't read a later revision
   EX=(':(top)'
       ':(exclude,top)poetry.lock' ':(exclude,top)frontend/package-lock.json'
       ':(exclude,top)*.env' ':(exclude,top)*.env.local' ':(exclude,top)*.env.*.local'
       ':(exclude,top)*.pem' ':(exclude,top)*.p12'
       ':(exclude,top)*service-account*.json' ':(exclude,top)*credentials.json')
   # (a) see everything FIRST — the exclusion decision has to happen before the file
   #     is written, or the secrets are already in it
   git diff main...HEAD --numstat --no-renames
   # (b) write the diff, then take the tier input AND file list from the SAME pathspec
   git diff main...HEAD -- "${EX[@]}" > "$P"
   git diff main...HEAD --numstat --no-renames -- "${EX[@]}"
   # (c) uncommitted + untracked work, WITHOUT touching the index
   git ls-files --others --exclude-standard -- "${EX[@]}" \
     | while read -r f; do git diff --no-index /dev/null "$f"; done >> "$P"
   git diff -- "${EX[@]}" >> "$P"
   git diff --cached -- "${EX[@]}" >> "$P"
   ```
   - **Never `git add -N`** to surface untracked files. It writes an intent-to-add
     entry that persists (so `git status --porcelain --untracked-files=no` reports it as
     a tracked change forever, wedging `/vps-loop-run` Step 1), makes every later
     `git stash push` fail with `Entry '…' not uptodate`, and — since the pathspec isn't
     applied to it — can stage the very paths `EX` withholds. `git diff --no-index` is
     read-only.
   - `--no-renames` matters: a rename prints `0  0  old => new`, so a pure-rename PR
     sums to **zero** changed lines (narrowest fan-out, on the diff shape `consistency`
     exists for) and hands reviewers a path that doesn't exist. Binary files print
     `-`/`-` — not zero.
   - Env excludes are `*.env`-shaped, not root-anchored, so `frontend/.env.local` is
     caught too. `*.example` / `*.sample` templates hold no values and stay reviewable.
   - **Do not exclude on name alone.** A bare `*credential*` swallows
     `tests/api/test_cors_credentials.py`; a `KEY`/`SECRET`/`TOKEN`/`PASSWORD` name
     filter swallows `.github/workflows/secrets-scan.yml` (the secret-scan gate
     itself), `db/migrations/0003_api_keys.*.sql`, and `frontend/src/styles/tokens*.ts`
     — hiding exactly the surface a `security` pass exists to read. Exclude only files
     that plausibly *carry* credential material (dotenv, keystores, service-account
     JSON, `*.pem`/`*.p12`); for source, config, migration, and workflow files keep the
     hunks and scan them for literal values instead.
   - If step (a)'s unfiltered list shows a credential-carrying path not already in
     `EX`, add it before step (b) runs, report it as a finding, and recommend rotation.
     Establish presence without echoing values — `grep -oE '^[A-Z_]+=' <file>` per
     CLAUDE.md — or simply recommend rotation.
   - Name every excluded path in the intro, from step (a)'s list alone.
   - Sum insertions + deletions from step (b)'s numstat for the fan-out row input, plus
     the line counts of anything step (c) appended.
2. Run `git status --porcelain` for the record. State in the intro anything step (c)
   did not fold in (e.g. an ignored-but-modified file), so "not reviewed" is explicit
   rather than assumed.
3. Deduce the branch objective and how the new code builds on `main`.
4. Write a short context intro stating that objective before any findings.
5. Risk tier — keyed on whether something *executes* the file, not on its path:
   - **Trivial** — human-facing prose only (a README, `docs/**`, a user-manual page),
     zero code/config/script. Skips Phase 2 entirely; one-pass gate.
     **Never trivial:** anything a session, shell, or CI run executes — every `.md`
     under `.claude/**` (commands, agents, skills, hooks, settings) and the root
     `CLAUDE.md` included, whatever the extension.
   - **Process-doc** — the diff touches `.claude/**` or the root `CLAUDE.md` and
     *nothing outside them*. Full dimension coverage and full 3-pass gate, exactly as
     standard tier — only two deltas: (a) skip step 6's test-delta gate, since these
     files have no `tests/` line share by nature — but if the diff touches
     `.claude/hooks/**`, `.claude/settings.json`, or any executed script, step 6's
     positive+negative verification-evidence requirement STILL applies (that's a
     security control; only the line-count math is inapplicable); (b) additionally
     require that every rule
     the diff adds names ONE canonical home, other mentions being pointers rather than
     copies — a rule duplicated across files, or a rule living only in a file nothing
     loads, is a Major finding. `security` is never optional here: judging whether a
     reword thinned a rail *is* the security review, and that's the diff most likely to
     be scored "no rail touched".
   - **Standard** — everything else, including a diff that touches `.claude/**` *and*
     code (that combination is standard, not process-doc, so the test-delta gate still
     applies; layer the canonical-home rule on top).
6. Test-delta gate (standard tier only): compare lines changed under `tests/` and
   `frontend/src/**/*.{test,spec}.{ts,tsx}` (this repo's tests are colocated next to
   source per `frontend/vitest.config.ts`, including under nested `__tests__` dirs —
   the `**` glob matches both) against total lines changed. If the test share is
   under 15% AND the diff adds new logic (not a pure refactor/wiring/config change),
   report this as a Major finding before dimension findings arrive — name which
   new/changed functions or components have no apparent matching test.
   **Exemption:** a diff whose only substantive changes are a lint rule, CI check, git
   hook, or static-analysis gate (the `enforcement` surface — see
   `branch-reviewer.md`) is exempt from this line-count gate. Instead, confirm the PR
   description/commit documents a positive+negative verification (violation caught,
   legitimate code passes) — if that evidence is missing, report THAT as the Major
   finding instead of a bare test-coverage percentage.

## Phase 2 — Fresh-context review (dispatch subagents)
**Trivial tier: skip this phase entirely** — read the doc diff yourself and go to
Phase 3.

`.claude/agents/branch-reviewer.md` is the single source of truth for the dimension
list, and this paragraph is the single statement of coverage: **always cover bugs,
logic, consistency, perf, practices, security, alternatives** — for every non-trivial
tier, process-doc included. Additionally cover `enforcement` ONLY when the diff
touches `.claude/hooks/`, `.claude/settings.json` (both hooks are *registered* there,
so a one-line edit disables a gate without touching `hooks/`),
`frontend/eslint.config.js`, `.github/workflows/`, `pyproject.toml` lint/type config,
or a new/changed `scripts/check-*` script.

**Fan-out row — take the FIRST matching row.** ("Row" is the fan-out; "tier" is the
risk tier from Phase 1 step 5. They're separate.) "Changed lines" = insertions +
deletions summed from the Phase 1 excluded `--numstat`. Use hard comparisons, and when
a diff sits near a bound take the next row **down this list** (more calls) — widening
early costs nothing, since the row never narrows later. The bounds exist so one merged call's diff plus its targeted
reads still fit in a single context with room for evidence gathering — that's what to
preserve if you ever retune them.
- **`<= 150` lines in 1–2 files, one layer** ("layer" = backend API / frontend /
  pipeline / infra; a `.claude/**`-only diff counts as one layer) → 3 base calls: `bugs+logic` /
  `perf+security` / `practices+consistency+alternatives`.
- **`<= 600` lines, any file count, one layer** → 5 base calls: `bugs+logic` /
  `consistency` / `perf` / `security` / `practices+alternatives`.
- **Anything else** — over 600 lines, spanning layers (API + frontend + pipeline), or
  no row above matched → one call per dimension.

**Escalations — size-independent, because these dimensions' risk doesn't track diff
size.** When one fires, that dimension is **removed from its merged group and
dispatched standalone**; the group keeps its remaining members (and is dropped if
emptied). So a row costs its base calls +1 per escalation that actually splits a group
— a dimension the row already dispatches standalone (e.g. `security` on the 5-call row)
needs no extra call, and the escalation is a no-op there.
- `security` — the diff touches auth/session/admin routes, `require_admin`, env or
  secret handling, a user-supplied URL, a PII path, or `.claude/**` / root `CLAUDE.md`
  (a reworded rail is invisible without a dedicated look).
- `consistency` — the diff renames or removes an identifier, or changes a Pydantic
  model/route field, an `agg_*` column, an i18n key, a `_LOCALES` entry, or — in
  `.claude/**` — a section, step number, command, or agent that another file
  references by name or number.
- `enforcement` — always its own call when it applies.
On the one-call-per-dimension row the escalations are already satisfied and add
nothing.

A merged call is told every dimension it owns and reports findings per dimension —
merging reduces call count, never coverage.

**Hand over a diff PATH, not diff text.** Give each subagent: the absolute path to the
Phase 1 diff file, the changed-file list, the Phase 1 objective statement, its
dimension(s), the worktree absolute path if the branch lives in one, and optionally a
brief that *adds* checks to a dimension (never one that removes a dimension's baseline).
Nothing else; no subagent sees another's output.
Do NOT paste the diff into the prompts — that re-serializes it as generated output once
per call, costing far more than the single Bash call it saves, and a large diff can
exceed one turn's output budget and be silently truncated.
Include this line verbatim, naming what Phase 1 left out:
`Deliberately excluded, do NOT re-derive: <paths>. This is not truncation.`
Without it, a reviewer sees a file named in the list with no hunks, follows its
own "re-derive what's missing" rule, and pulls back the full unfiltered diff —
inverting the saving and defeating the secret exclusion.

Reviewers already carry the targeted-read and don't-re-diff rules in their own prompt
— don't restate them per dispatch.

**Worktree hazard.** Dispatched reviewers have Bash and read the worktree concurrently.
While any is running, do NOT `git checkout` / `switch` / `reset` in that worktree — read
other revisions with `git show <rev>:<path>` instead — and re-verify HEAD once they all
return, so findings are known to be against the revision you diffed.

**If a PR already exists for this branch** — per CLAUDE.md the review normally runs
*before* the PR is opened, so skip this block unless a PR number is already known (from
`/pr-github`, `/address-my-pr-comments`, or the caller). Don't spend a query per pass
looking for a PR that by policy shouldn't exist yet.
With a number in hand, fetch its threads once (`gh api
repos/{owner}/{repo}/pulls/<number>/comments --paginate`) and drop a candidate finding
ONLY when an existing thread makes the same point at the same location AND the current
code demonstrably addresses it. A thread merely existing is NOT evidence of a fix —
`/pr-github` posts findings as threads, so a topic-level drop would silently void
passes 2 and 3 of the gate and let an unfixed Major through. Anything else gets
reported, marked `(already raised in <thread url> — still open)`, and counts toward the
Major gate as normal.

Then YOU synthesize: dedupe, rank by severity, drop low-confidence noise. Keep only
findings that affect correctness or the objective.

## Phase 3 — Cleanup (only after review reported)
- Remove unnecessary comments and dead code in the diff.
- Add docstrings: file header + new/changed funcs and classes.
- Run `make check` (fmt + lint + test). DB SAFETY: tests must point at the throwaway
  Postgres — `DATABASE_URL=postgresql://transit:transit@localhost:5544/transit_test`
  — and throwaway ClickHouse on :8124. NEVER let a run hit dev Postgres :5433 or the
  dev ClickHouse (`transit-ch`). See CLAUDE.md / transit-app-gotchas.
- Fix only errors related to the changed files.
- Before reporting the flow complete, invoke `superpowers:verification-before-completion`
  and show the actual `make check` output — no success claims without evidence.

## Review gate
Standard and process-doc tiers: before a PR is considered ready, run this whole flow
THREE times, each pass approaching the diff as if seeing it for the first time (reset
mindset between passes). Each fresh read surfaces issues the prior context-anchored
read glossed over.
Each pass re-classifies the **cumulative** `main...HEAD` diff as it stands then. Risk
tier and fan-out row are both **monotonic across the gate**: they may widen, never
narrow (a fix that reverts a line can shrink the diff — the floor still holds). Once a branch has qualified for
one-call-per-dimension, or for a dedicated `security` / `consistency` / `enforcement`
call, it keeps them for every remaining pass — state the tier and call plan in each
pass's intro so the next pass can honour that floor. Don't add calls merely because
it's a fresh pass; only because the diff grew or an escalation newly applies.
Trivial tier: one pass is enough — there's no dimension-review or fix-iterate loop to
re-run against.

## Boundaries
- Do NOT commit. Do NOT push.
- If work is in a git worktree, all changes go to the worktree — run git via
  `git -C <worktree-abs-path>` and confirm commits land on the feature branch, not
  `main` (a subagent's default cwd is the main repo on `main`).
