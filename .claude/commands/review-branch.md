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
1. Compute the diff ONCE, into a file, against `main` (NOT master), excluding
   lockfiles and generated files (they carry no review value and would be shipped to
   every reviewer):
   ```bash
   D=<scratchpad>/branch.diff
   git diff main...HEAD -- . ':(exclude)poetry.lock' ':(exclude)frontend/package-lock.json' > "$D"
   git diff main...HEAD --stat            # keep this; it's the file list
   git diff main...HEAD --shortstat       # keep this; it's the tier input
   ```
   (`git -C <worktree-abs-path>` in a worktree.) Phase 2 hands subagents that **path**,
   never inlined diff text. If a lockfile did change, say so in the intro from `--stat`
   alone ("`poetry.lock` +N/-M, dependency bump — reviewed by stat only").
   You need only `--stat`/`--shortstat` yourself to tier the diff and state the
   objective — don't pull a large diff into your own context.
2. Run `git status --porcelain` too. Uncommitted work is NOT in `main...HEAD`: either
   append `git diff` / `git diff --cached` to the handed-over file, or state in the
   intro that uncommitted changes were not reviewed.
3. Deduce the branch objective and how the new code builds on `main`.
4. Write a short context intro stating that objective before any findings.
5. Risk tier — three-way, and it keys on *what executes the file*, not its extension:
   - **Trivial** — human-facing prose only (`README.md`, `docs/**`), zero code/config/
     script. Skips Phase 2 entirely; one-pass gate.
     **Never trivial:** anything under `.claude/**` (commands, agents, skills, hooks,
     settings) or the root `CLAUDE.md`. Those are instructions a future session
     executes, so a defect there silently degrades every later run.
   - **Process-doc** — the diff touches `.claude/**` or the root `CLAUDE.md`. Full
     3-pass gate; Phase 2 covers `practices`, `logic`, `consistency`, `alternatives`,
     plus `security` if it touches an approval gate, a DB-safety rail, or secrets
     handling, plus `enforcement` if a hook/CI gate changed. Skip step 6's test-delta
     gate — these files have no `tests/` share by nature. In its place: every rule the
     diff adds must name ONE canonical home, with other mentions as pointers rather
     than copies. A rule duplicated across files, or a rule living only in a file
     nothing loads, is a Major finding.
   - **Standard** — everything else: full dimension coverage (fan-out per Phase 2),
     full 3-pass gate, no shortcuts.
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
list. Always cover: bugs, logic, consistency, perf, practices, security, alternatives.
Additionally cover `enforcement` ONLY when the diff touches `.claude/hooks/`,
`frontend/eslint.config.js`, `.github/workflows/`, `pyproject.toml` lint/type config,
or a new/changed `scripts/check-*` script.

**Fan-out — take the FIRST matching row.** "Changed lines" = insertions + deletions
from the Phase 1 `--shortstat` (lockfiles/generated files already excluded).
- **Under ~150 lines in 1–2 files** → 3 calls: `bugs+logic` / `perf+security` /
  `practices+consistency+alternatives`.
- **Under ~600 lines, any file count** → 5 calls: `bugs+logic` / `consistency` /
  `perf` / `security` / `practices+alternatives`.
- **Anything else** (over ~600 lines, spanning layers, or no row above matched) →
  one call per dimension.

**Size-independent escalations, applied on top of the matched row** — these dimensions
don't correlate with diff size, so they get their own call regardless:
- `security` — the diff touches auth/session/admin routes, `require_admin`, env or
  secret handling, a user-supplied URL, or a PII path.
- `consistency` — the diff renames or removes an identifier, or changes a Pydantic
  model/route field, an `agg_*` column, an i18n key, or a `_LOCALES` entry.
- `enforcement` — always its own call when it applies.

A merged call is told every dimension it owns and reports findings per dimension —
merging reduces call count, never coverage.

**Hand over a diff PATH, not diff text.** Give each subagent exactly: the absolute
path to the Phase 1 diff file, the `--stat` file list, the Phase 1 objective
statement, and its dimension(s). Nothing else; no subagent sees another's output.
Do NOT paste the diff into the prompts — that re-serializes it as generated output
once per call, which costs far more than the single Bash call it would save, and a
large diff can exceed one turn's output budget and be silently truncated.
If the diff touches a secret-bearing path (`.env*`, credential/service-account JSON,
`*.pem`, anything named `*KEY*`/`*SECRET*`/`*TOKEN*`), don't hand those hunks over:
pass the path with a redacted note, report it as a finding, and recommend rotation if
a value is present.

Reviewers already carry the targeted-read and don't-re-diff rules in their own prompt
— don't restate them per dispatch.

**If a PR already exists for this branch** — per CLAUDE.md the review normally runs
*before* the PR is opened, so this is a no-op on round 1. Check with
`gh pr view --json number -q .number`; if there's none, skip this and say so.
Otherwise fetch its threads once (`gh api repos/{owner}/{repo}/pulls/<number>/comments
--paginate`) and drop a candidate finding ONLY when an existing thread makes the same
point at the same location AND the current code demonstrably addresses it. A thread
merely existing is NOT evidence of a fix — `/pr-github` posts findings as threads, so
a topic-level drop would silently void passes 2 and 3 of the gate and let an unfixed
Major through. Anything else gets reported, marked `(already raised in <thread url> —
still open)`, and counts toward the Major gate as normal.

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
call, it keeps them for every remaining pass. A fresh pass means resetting mindset,
not adding calls.
Trivial tier: one pass is enough — there's no dimension-review or fix-iterate loop to
re-run against.

## Boundaries
- Do NOT commit. Do NOT push.
- If work is in a git worktree, all changes go to the worktree — run git via
  `git -C <worktree-abs-path>` and confirm commits land on the feature branch, not
  `main` (a subagent's default cwd is the main repo on `main`).
