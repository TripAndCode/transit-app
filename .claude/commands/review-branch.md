---
name: review-branch
description: Senior-staff review of the current branch vs main using fresh-context subagents, then safe cleanup.
---

Review the current branch for project $ARGUMENTS as a principal engineer.

## Skills (invoke these every run)
- `superpowers:systematic-debugging` — whenever a test, lint, or build check fails
  in Phase 3, before proposing any fix.
- `superpowers:verification-before-completion` — before claiming the review or
  cleanup is done; report evidence, not assertions.
- `postgres-perf` / `maplibre-map` — invoke ONLY when the diff touches ClickHouse/
  Postgres queries or MapLibre layers, respectively.

## Phase 1 — Understand (you, directly)
1. Diff the current branch against `main` (NOT master).
2. Deduce the branch objective and how the new code builds on `main`.
3. Write a short context intro stating that objective before any findings.
4. Test-delta gate: compare lines changed under `tests/` and
   `frontend/src/**/*.{test,spec}.{ts,tsx}` (this repo's tests are colocated next to
   source per `frontend/vitest.config.ts`, including under nested `__tests__` dirs —
   the `**` glob matches both) against total lines changed. If the test share is
   under 15% AND the diff adds new logic
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
- Before reporting the flow complete, invoke `superpowers:verification-before-completion`
  and show the actual `make check` output — no success claims without evidence.

## Review gate
Before a PR is considered ready, run this whole flow THREE times, each pass
approaching the diff as if seeing the PR for the first time (reset mindset between
passes). Each fresh read surfaces issues the prior context-anchored read glossed over.

## Boundaries
- Do NOT commit. Do NOT push.
- If work is in a git worktree, all changes go to the worktree — run git via
  `git -C <worktree-abs-path>` and confirm commits land on the feature branch, not
  `main` (a subagent's default cwd is the main repo on `main`).
