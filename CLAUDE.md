# CLAUDE.md

Repository rules that are unsafe or expensive to rediscover. Architecture, setup,
and feature walkthroughs live in `README.md` and `docs/features/`; load them only when
the task needs them.

## Architecture pointers

- Data path: `gtfs_pipeline.py` → `pipeline/analyze.py` → `agg_*` tables → FastAPI
  routers → React SPA.
- Raw GTFS-RT `updates` is in ClickHouse. Postgres holds aggregates, OLTP, PostGIS,
  and pgvector. Default reports use precomputed aggregates; narrow filters may scan
  ClickHouse.
- Ask routing is rules → embedding nearest-neighbour → RAG LLM. Only the third stage
  calls an LLM.

## Database safety

- Dev Postgres `localhost:5433/transit` and dev ClickHouse `transit-ch` contain real
  data and are read-only for agents. SELECT/EXPLAIN is allowed; never run writes,
  DDL, resets, down migrations, or destructive Make targets against them.
- Tests use throwaway Postgres `:5544/transit_test` and ClickHouse `:8124`. Before a
  DB test, load `transit-app-gotchas` for the complete environment block and image
  requirements.
- ClickHouse-gated tests silently skip unless `RUN_CH_INTEGRATION=1` and the
  `CLICKHOUSE_*` test variables are set. A passing run with skips is not full
  integration evidence.
- Put pure logic tests under `tests/unit/`; that directory bypasses DB fixtures.
  Mock the ML embedder unless a test is explicitly slow.

## Verification commands

- Backend: `make serve`, `make test`, `make check`, `poetry run ruff check`,
  `poetry run mypy`. Never let `make test/check` inherit the default `:5433` URL;
  point it at `:5544`.
- Frontend: `npm run typecheck`, `npm run test`, `npm run lint`, `npm run lint:i18n`,
  `npm run lint:i18n-strings`, `npm run test:check-entry-chunk`, then
  `npm run build:bundle && npm run check:entry-chunk`.
- Run the smallest relevant check during iteration and the required complete check
  once before completion. Capture verbose output to a file and surface only the
  useful summary or failure tail.
- After analyze changes, rebuild affected aggregates. Use `make analyze-all` for all
  agencies and `make check-aggs` to detect stale aggregates.

## Frontend and user-facing text

- React Compiler is enabled. Do not add `useMemo`, `useCallback`, or `React.memo` as
  performance fixes. Use `useEffectEvent` for fresh props in stable handlers; never
  write refs during render.
- `react-hooks/set-state-in-effect` and `react-hooks/purity` are errors. Prefer
  derived state over synchronization effects.
- All visible strings use `t()` with matching keys in both
  `frontend/src/i18n/locales/{ja,en}.json`. Intentional source-language exceptions
  require `i18n-ignore`.
- Keep UI calm: no alarm-red defaults, dense panels, or stressful motion.
- New pages are lazy-loaded. Keep MapLibre out of the entry chunk.
- Server-side strings live in `_LOCALES` in `pipeline/query/tools.py`; update both
  languages and exact-string tests together.

## LLM features

- Prefer deterministic SQL tools. New LLM-grounded behavior needs an environment
  kill switch, graceful disabled path, and objective stopping criterion.

## Git and pull requests

- Base branch is `main`; squash merge with Conventional Commit subjects.
- Every PR runs `/review-branch` at least twice before opening as a draft: an
  initial invocation, then a second full invocation on the (possibly fixed) diff
  before opening, even if the first found nothing worth fixing. Each invocation
  uses one pass and two merged reviewers for normal changes, with extra review
  only for enforcement, high-risk paths, or material fixes. Human prose outside
  `.claude/**` may use its direct trivial path; `.claude/**` and this file are
  executable process docs.
- Open PRs as drafts. Mark ready only after both required `/review-branch` passes
  are clean. Once ready and GitHub reports the PR mergeable/clean (no conflicts),
  it may be squash-merged — by an interactive session or by `/vps-loop-run` itself
  — then run `/cleanup-merged` to remove the now-stale branch/worktree. A
  `CONFLICTING`/`DIRTY` merge state must be resolved (merge latest `main`, resolve
  conflicts, re-run both review passes on the result) before readying or merging.
  Every PR body states `**Origin:** Interactive session` or `**Origin:** Autonomous
  VPS loop (item N)`.
- CI is currently skipped: every commit message includes `[skip ci]` as its own
  line/trailer. Local verification and the pre-push hook are therefore mandatory.
- For stacked PRs, retarget dependants to `main` before deleting their base branch;
  GitHub otherwise closes them.
- After a PR merge, run `/cleanup-merged` in persistent local/VPS clones. Its
  `scripts/cleanup_git_state.py` dry run is the deletion authority: only clean local
  worktrees and branches proven recoverable from `main` or an exact merged-PR head
  may be removed. Remote branches, `production`, and unique post-merge commits stay.

## Process rules

- A mistake repeated twice is a missing guardrail. Capture it in the relevant skill
  during the second session.
- Promote recurring rules up the enforcement ladder: code structure → static
  analysis/tests → hooks → skills → this file. Do not keep expanding always-loaded
  prose when a deterministic check can enforce the invariant.
- Keep one canonical home per rule. Other files should point to it instead of copying
  its rationale and edge cases.

## Autonomous VPS loop

- `/vps-loop-run` is the canonical state machine. `NEXT_TASK.md` is its untracked
  input and status log; one run advances at most one item.
- The loop may create worktrees, commit, push feature branches, open draft PRs,
  mark its own PR ready, and squash-merge it once both required `/review-branch`
  passes are clean and GitHub reports the PR mergeable/clean (Step 5 gates this
  unconditionally before Step 6 runs) — then run `/cleanup-merged` to remove the
  now-stale branch/worktree. It never pushes directly to `main` (only via a
  reviewed, merged PR), never force-pushes, and never bypasses the two-pass review
  gate or a `CONFLICTING` merge state to force a merge through.
- Shared hooks apply on the VPS. VPS-only permissions live in ignored
  `.claude/settings.local.json` and must never be committed.
- Operational setup, non-interactive-shell environment rules, and current timeout
  limitations are documented in `.claude/README.md`, not repeated in every session.
