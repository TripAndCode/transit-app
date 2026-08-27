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
1. Compute the diff ONCE, into a file at an **absolute** path, against `main` (NOT
   master). All pathspecs use `:(top)` so they resolve from the repo root, not the
   current directory — a bare `-- .` run from `frontend/` silently drops every backend
   hunk:
   ```bash
   D="<scratchpad>/branch.diff"          # absolute; scratchpad dir, not /tmp
   EX=(':(top)'
       ':(exclude,top)poetry.lock' ':(exclude,top)frontend/package-lock.json'
       ':(exclude,top).env' ':(exclude,top).env.local' ':(exclude,top).env.*.local'
       ':(exclude,top)*.pem' ':(exclude,top)*credential*' ':(exclude,top)*service-account*')
   git diff main...HEAD -- "${EX[@]}" > "$D"
   git diff main...HEAD --numstat -- "${EX[@]}"   # tier input AND file list
   git diff main...HEAD --numstat                 # unfiltered, to see what was excluded
   ```
   Exclusions are **structural, in this command** — never "hand the path over and add a
   note", because the note can't redact a file the reviewer is told to read.
   - `--numstat` (excluded) is the single source for both the tier input (sum of
     insertions + deletions) and the changed-file list. Don't use `--stat`/`--shortstat`:
     unfiltered totals mis-tier a diff (a 40-line change with 6k lines of lock churn
     would land in the widest tier, which is exactly what the exclusion exists to stop).
   - Templates (`*.example`, `*.sample`) hold no values and ARE reviewable — don't
     exclude them.
   - If the unfiltered `--numstat` lists any other path whose name carries
     `KEY`/`SECRET`/`TOKEN`/`PASSWORD`/`CREDENTIAL` and isn't a template, add it to `EX`
     before writing the file, report it as a finding, and recommend rotation if a value
     is present (per CLAUDE.md's secrets rule).
   - Report every excluded path in the intro from the unfiltered `--numstat` alone
     ("`poetry.lock` +N/-M, dependency bump — reviewed by numstat only").
2. Run `git status --porcelain`. Uncommitted work is NOT in `main...HEAD`. Either fold
   it in — `git add -N` the untracked paths first (plain `git diff` never shows them),
   then append `git diff -- "${EX[@]}"` and `git diff --cached -- "${EX[@]}"` (same
   pathspec — an unfiltered append re-opens the secret and lockfile holes) and add
   their numstat totals to the tier input — or state in the intro that uncommitted
   changes were not reviewed, naming them.
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
     files have no `tests/` share by nature; (b) additionally require that every rule
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
touches `.claude/hooks/`, `frontend/eslint.config.js`, `.github/workflows/`,
`pyproject.toml` lint/type config, or a new/changed `scripts/check-*` script.

**Fan-out — take the FIRST matching row.** "Changed lines" = insertions + deletions
summed from the Phase 1 excluded `--numstat`. Use hard comparisons, and round *up* a
row when a diff sits near a bound (the tier is monotonic anyway, so widening early
costs nothing later). The bounds exist so one merged call's diff plus its targeted
reads still fit in a single context with room for evidence gathering — that's what to
preserve if you ever retune them.
- **`<= 150` lines in 1–2 files, one layer** → 3 base calls: `bugs+logic` /
  `perf+security` / `practices+consistency+alternatives`.
- **`<= 600` lines, any file count, one layer** → 5 base calls: `bugs+logic` /
  `consistency` / `perf` / `security` / `practices+alternatives`.
- **Anything else** — over 600 lines, spanning layers (API + frontend + pipeline), or
  no row above matched → one call per dimension.

**Escalations — size-independent, because these dimensions' risk doesn't track diff
size.** When one fires, that dimension is **removed from its merged group and
dispatched standalone**; the group keeps its remaining members (and is dropped if
emptied). So a row is "3 base calls, +1 per escalation", not 3 calls flat.
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
Each pass re-tiers against the **cumulative** `main...HEAD` diff as it stands then —
fix commits add to it, so it grows and never shrinks. The tier is **monotonic across
the gate**: it may widen, never narrow. Once a branch has qualified for
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
