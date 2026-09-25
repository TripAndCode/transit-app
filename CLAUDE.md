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
- Admin control room: `api/routers/admin*.py` (board, agencies, users, audit, flags,
  ask ops) behind `RequireAdmin`/`require_admin`. See `docs/features/admin-control-
  room.md` for routes, endpoints, and tables per section.
- `pipeline/flags.py` is the one read path for feature kill switches: a DB
  `feature_flags` override wins over the env default, cached process-wide for 30s,
  invalidated immediately on a PATCH. Never read a flag's env var directly.
- `pipeline/runs.py` records pipeline jobs into `pipeline_runs`; `api/admin_audit.py`
  records every admin mutation into `admin_audit`. Both feed the admin board/audit log
  and never let bookkeeping failure break the underlying job or request.

## Database safety

- Dev Postgres and dev ClickHouse (`docker compose exec clickhouse`) contain real
  data and are read-only for agents. SELECT/EXPLAIN is allowed; never run writes,
  DDL, resets, down migrations, or destructive Make targets against them. Dev
  Postgres is whichever port the live container publishes — `compose.yml` declares
  `:5433`, but the instance holding the data can be published elsewhere, so read
  `DATABASE_URL` rather than assuming. `.claude/hooks/guard_dev_db.py`'s
  `DEV_PORTS` is the enforced list and must name every such port.
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
  `poetry run ruff format --check`, `poetry run mypy`. Never let `make test/check`
  inherit the default `:5433` URL; point it at `:5544`. `make fmt` rewrites files
  rather than reporting, so it does not verify formatting.
- Frontend: `npm run typecheck`, `npm run test`, `npm run lint`, `npm run lint:i18n`,
  `npm run lint:i18n-strings`, `npm run deadcode`, `npm run test:check-entry-chunk`,
  `npm run test:check-css-tokens`, `npm run check:css-tokens`, then
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
- Every PR runs `/review-branch` once before opening as a draft. That invocation
  uses one pass and two merged reviewers for normal changes, with extra review
  only for enforcement, high-risk paths, or material fixes. Human prose outside
  `.claude/**` may use its direct trivial path; `.claude/**` and this file are
  executable process docs.
- Open PRs as drafts. Mark ready only after the required `/review-branch` pass
  is clean. Once ready and GitHub reports the PR mergeable/clean (no conflicts)
  AND CI is green on the PR's head AND `main` has not advanced since that pass
  ran, it may be squash-merged
  — by an interactive session or by `/vps-loop-run` itself — then run
  `/cleanup-merged` to remove the now-stale branch/worktree. GitHub's
  `mergeable`/`mergeStateStatus` alone does NOT catch a `main` that moved on
  without a textual conflict (this repo has no branch-protection "must be
  up to date" rule to surface that as `BEHIND`) — check the actual SHA. Either a
  `CONFLICTING`/`DIRTY` state or `main` having advanced at all requires the same
  fix: merge latest `main`, resolve any conflicts, and re-run the review pass
  on the result before readying or merging. Every PR body states `**Origin:**
  Interactive session` or `**Origin:** Autonomous VPS loop (item N)`.
- CI must be green on the PR's head before it merges. Branch commits carry no
  `[skip ci]`: every push to a PR runs CI, which is what the gate reads. Only
  the squash-merge commit keeps the trailer, so `main` does not re-run what the
  branch already proved. `transit-app-gotchas` owns the mechanism and its traps
  — chiefly that a tip which does carry the trailer produces no run at all, and
  the gate then has nothing to read rather than something to fail.
- Local verification stays mandatory regardless: CI sees only the tip that
  triggered it, and the pre-push hook's file-scoped checks cover only the pushed
  worktree's changed Python — its own header states what it leaves uncovered.
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

## Durable content only

- Comments and docstrings are not a second copy of the code. Keep them only when
  they explain non-obvious rationale, a durable invariant, a security or data-safety
  boundary, a public contract, or a nontrivial algorithm. Remove comments that
  restate the next line, describe a function's name or arguments, narrate routine
  control flow, or preserve historical phase/PR/session context. Prefer clear names,
  small functions, and tests as the durable explanation. During housekeeping, delete
  redundant prose and rewrite stale rationale instead of preserving it for
  completeness.
- Comments and docs state current, permanent facts, never who changed what or when.
  Do not cite a PR/issue number, a past bug, or a date as the reason code looks the
  way it does; state the underlying invariant directly instead (e.g. "ties break on
  route_code ascending because the GROUP BY gives no ordering guarantee within a tie"
  — not "fixed in PR #196"). The task/PR narrative belongs in the PR description and
  commit history, not the source tree.
- Do not hardcode a measured benchmark (a specific row count, latency, or duration) as
  if it were a fixed fact — live datasets and hardware drift, so a "measured 46.5s on
  574M rows" comment goes stale silently and misleads the next reader. State the
  durable design rationale instead; an order-of-magnitude scale ("hundreds of millions
  of rows") is fine when it adds real intuition, a specific decaying number is not.
- Do not commit a markdown file that is a log of a past dev/refactor session (dated
  entries, "found X, fixed Y", slice-by-slice narrative) as permanent repo content —
  that belongs in the PR body or commit message, not a tracked file. `docs/refactor-
  log.md` is the one deliberate exception: it is `/vps-loop-run`'s own required
  operational trail (see `.claude/commands/vps-loop-run.md`), not free-standing dev
  narration, and stays out of this rule.

## Autonomous VPS loop

- `/vps-loop-run` is the canonical state machine. `NEXT_TASK.md` is its untracked
  input and status log; one run advances at most one item.
- The loop may create worktrees, commit, push feature branches, open draft PRs,
  mark its own PR ready, and squash-merge it once the required `/review-branch`
  pass is clean, GitHub reports the PR mergeable/clean, AND `main` hasn't
  advanced since that pass ran (Step 5 gates this unconditionally before Step
  6 runs; Step 6 re-checks the `main` SHA immediately before merging, since a
  non-conflicting advance is invisible to `mergeable`/`mergeStateStatus` alone)
  — then run `/cleanup-merged` to remove the now-stale branch/worktree. It never
  pushes directly to `main` (only via a reviewed, merged PR), never force-pushes,
  and never bypasses the review gate, a `CONFLICTING` merge state, or a
  `main` that moved on to force a merge through.
- Shared hooks apply on the VPS. VPS-only permissions live in ignored
  `.claude/settings.local.json` and must never be committed.
- Operational setup, non-interactive-shell environment rules, and current timeout
  limitations are documented in `.claude/README.md`, not repeated in every session.
- For a `NEXT_TASK.md`-tracked backlog item, the VPS loop is the default place
  that work happens, not an interactive session (this doesn't apply to ordinary
  interactive feature work outside the backlog, e.g. work requested directly in
  a session — see `## Git and pull requests` above). Prefer reporting status and
  letting the next tick continue over fixing/finishing a stuck or blocked item
  yourself; only take over in-progress worker state when explicitly asked to. A
  specific ask to intervene ("if it stops, resolve it" / "fix the root cause")
  authorizes that one intervention — not a chain into full interactive
  development of everything downstream. After finishing the thing that was
  actually asked for, check whether the loop's next tick can plausibly continue
  from here; if so, stop and let it, rather than proactively continuing the
  chain of related fixes yourself. (This is a session-discipline rule, not a
  code-enforceable one, so it skips the usual skill-first promotion ladder in
  `## Process rules` — there's no existing skill for session behavior to have
  captured it in.)
