---
name: review-branch
description: Senior-staff review of the current branch vs main using fresh-context subagents, then safe cleanup.
---

Review the current branch for project $ARGUMENTS as a principal engineer.

## Phase 1 — Understand (you, directly)
1. Diff the current branch against `main` (NOT master).
2. Deduce the branch objective and how the new code builds on `main`.
3. Write a short context intro stating that objective before any findings.
4. Test-delta gate: compare lines changed under `tests/` and
   `frontend/src/**/*.{test,spec}.{ts,tsx}` (this repo's tests are colocated next to
   source per `frontend/vitest.config.ts`, not under a `__tests__` dir) against
   total lines changed. If the test share is under 15% AND the diff adds new logic
   (not a pure refactor/wiring/config change), report this as a Major finding
   before dimension findings arrive — name which new/changed functions or
   components have no apparent matching test.

## Phase 2 — Fresh-context review (dispatch subagents)
Dispatch the `branch-reviewer` subagent once per dimension listed under "Dimensions
you may be asked for" in `.claude/agents/branch-reviewer.md` — that file is the
single source of truth for the dimension list, so it doesn't drift out of sync with
this one. Run all dimensions in parallel, each with a clean context and the diff +
stated objective only — none sees another's output.
Then YOU synthesize: dedupe, rank by severity, drop low-confidence noise. Keep only
findings that affect correctness or the objective.

## Phase 3 — Cleanup (only after review reported)
- Remove unnecessary comments and dead code in the diff.
- Add docstrings: file header + new/changed funcs and classes.
- Run `make check` (fmt + lint + test). DB SAFETY: tests must point at the throwaway DB —
  `DATABASE_URL=postgresql://transit:transit@localhost:5544/transit_test`. NEVER let
  a run hit dev DB :5433. See CLAUDE.md / transit-app-gotchas.
- Fix only errors related to the changed files.

## Review gate
Before a PR is considered ready, run this whole flow THREE times, each pass
approaching the diff as if seeing the PR for the first time (reset mindset between
passes). Each fresh read surfaces issues the prior context-anchored read glossed over.

## Boundaries
- Do NOT commit. Do NOT push.
