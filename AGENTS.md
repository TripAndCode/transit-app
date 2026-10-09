# AGENTS.md

Operating rules for any coding agent in this repository: Claude Code, Codex, Cursor,
Gemini CLI, Copilot or anything else. It holds only rules that are unsafe or expensive
to rediscover. Architecture, setup and feature walkthroughs live in `README.md` and
`docs/features/`; read them only when the task needs them.

Every rule applies whether or not a tool enforces it for you. Claude Code loads this
file through `CLAUDE.md`; local Codex reads it directly. Claude's hooks live in
`.claude/settings.json`; local Codex loads `.codex/hooks.json` when the project is
trusted. Agents in sessions without active hooks must apply the same rules by hand.

Longer reference notes live in `.claude/skills/*/SKILL.md`; `.agents/skills/` links
to the same files for Codex discovery. They are plain Markdown any agent can read:
- `transit-app-gotchas`: test databases, i18n parity, worktrees, CI trailer mechanics;
- `postgres-perf`: query and aggregate performance traps;
- `maplibre-map`: Map tab conventions.

## Hard rules

- **Dev databases are read-only.** Dev Postgres and dev ClickHouse (`docker compose
  exec clickhouse`) hold real data. SELECT/EXPLAIN is allowed; never run writes, DDL,
  resets, down migrations, or destructive Make targets against them.
  - Dev Postgres is whichever port the live container publishes. `compose.yml`
    declares `:5433`, but the instance holding the data can be published elsewhere,
    so read `DATABASE_URL` rather than assuming.
  - `.claude/hooks/guard_dev_db.py`'s `DEV_PORTS` is the enforced list for Claude
    Code and must name every such port.
- **Tests and any writes go to throwaway databases only:** Postgres
  `:5544/transit_test` and ClickHouse `:8124`, or the isolated pair that
  `scripts/run_full_ci.sh` starts for each run.
- **The VPS ML replica is neither a dev store nor production.** `deploy/vps/compose.yml`
  rebuilds it from R2 and it takes writes; `docs/features/ml-forecast.md` covers it.
- **Never push to `main`, and never rewrite history another session may hold.**
  Several agents can share this clone. Don't force-push a branch you didn't create,
  and don't touch another session's worktree. Never `git stash pop`/`drop`/`clear`:
  the stash stack is shared by every worktree. To set work aside, make a WIP commit.
- **Never commit secrets.** `.env`, keys and tokens stay untracked. `make hooks`
  installs the gitleaks pre-commit hook; CI's `secrets-scan.yml` is the backstop.

## Architecture pointers

- Data path: `gtfs_pipeline.py` → `pipeline/analyze.py` → `agg_*` tables → FastAPI
  routers → React SPA.
- Raw GTFS-RT `updates` is in ClickHouse. Postgres holds aggregates, OLTP, PostGIS,
  and pgvector. Default reports use precomputed aggregates; narrow filters may scan
  ClickHouse.
- Ask routing is rules → embedding nearest-neighbour → RAG LLM. Only the third stage
  calls an LLM.
- Admin control room: `api/routers/admin*.py` (board, agencies, users, audit, flags,
  ask ops) behind `RequireAdmin`/`require_admin`. See
  `docs/features/admin-control-room.md` for routes, endpoints, and tables per section.
- `pipeline/flags.py` is the one read path for feature kill switches. A DB
  `feature_flags` override wins over the env default. The result is cached
  process-wide for 30s and invalidated immediately on a PATCH. Never read a flag's
  env var directly.
- `pipeline/runs.py` records pipeline jobs into `pipeline_runs`; `api/admin_audit.py`
  records every admin mutation into `admin_audit`. Both feed the admin board/audit log
  and never let bookkeeping failure break the underlying job or request.

## Working in a worktree, alongside other agents

- Do each branch in its own worktree under `.worktrees/` (gitignored). The main
  checkout stays on `main` and is shared. Don't leave a branch, uncommitted work or
  build output (`make bake` → `api/static/`) there.
- A fresh worktree has no virtualenv and no `node_modules`.
  - **virtualenv:** Poetry keys a virtualenv on the directory, so `poetry run` inside
    a worktree resolves a new, empty one. Route `poetry run` to the main checkout's
    environment (`poetry env info --path` from there) unless the branch changes
    `pyproject.toml`/`poetry.lock`. `transit-app-gotchas` covers the details.
  - **`node_modules`:** link the main checkout's `frontend/node_modules`, or run
    `npm ci`. After a fresh install on macOS, check that oxc-parser's native binding
    is present. Without it, `npm run deadcode` fails with a misleading module error.
- The `:5544`/`:8124` pair is shared by every session on the host. Two suites against
  it at once reset each other's tables and fail for reasons unrelated to either diff.
  For any run that might overlap with another, use `scripts/run_full_ci.sh`. It
  starts its own Postgres + ClickHouse on free ports and removes them afterwards.
- When a failure seems unrelated to your diff, reproduce it in your own worktree on
  isolated ports. Never reproduce it in the shared checkout.

## Verification commands

- Backend: `make serve`, `make test`, `make check`, `poetry run ruff check`,
  `poetry run ruff format --check`, `poetry run mypy`. `make fmt` rewrites files
  rather than reporting, so it does not verify formatting.
  - `make test`/`make check` force the throwaway `:5544`/`:8124` block via
    `scripts/run_integration_tests.sh`.
  - A bare `poetry run pytest` inherits the shell's `DATABASE_URL`/`CLICKHOUSE_*`, so
    run it only with the test environment block from `transit-app-gotchas`.
  - `scripts/run_full_ci.sh` mirrors CI's backend job (whole-repo ruff, mypy, the full
    suite with ClickHouse) on isolated ports.
- Frontend:
  - `npm run typecheck`
  - `npm run test:coverage`: the unit tests plus the `vitest.config.ts` coverage
    thresholds CI gates on; `npm run test` skips them
  - `npm run lint`, `npm run lint:i18n`, `npm run lint:i18n-strings`
  - `npm run deadcode`
  - `npm run test:check-entry-chunk`, `npm run test:check-css-tokens`,
    `npm run check:css-tokens`
  - `npm run test:check-react-compiler`, `npm run check:react-compiler`: the second
    fails on any component or hook the React Compiler skips
  - then `npm run build:bundle && npm run check:entry-chunk`

  Lint through `npm run lint`, never a bare `npx eslint`, which fails outright.
  `frontend/scripts/ts6-for-eslint.cjs` explains the compiler split and when it can
  go.
- Run the smallest relevant check while iterating, and the complete required set once
  before completion.
  - Verify by running the real command in the real environment. `make -n`, warm
    caches and mocks can each hide a defect.
  - Capture verbose output to a file and surface only the summary or failure tail.
- Claude Code's pre-push hook runs most of these checks on the pushed worktree; its
  header lists what it leaves out. Other agents run them before pushing. Either way,
  CI sees only the tip that triggered it, so local verification stays mandatory.
- After analyze changes, rebuild affected aggregates. Use `make analyze-all` for all
  agencies and `make check-aggs` to detect stale aggregates.

## Tests

- Before a DB test, read `transit-app-gotchas` for the complete environment block and
  the test image requirements. A stock `postgres` image lacks PostGIS, and migration
  0001 fails.
- ClickHouse-gated tests silently skip unless `RUN_CH_INTEGRATION=1` and the
  `CLICKHOUSE_*` test variables are set. A passing run with skips is not full
  integration evidence.
- Put pure logic tests under `tests/unit/`; that directory bypasses DB fixtures.
  Mock the ML embedder unless a test is explicitly slow.

## Frontend and user-facing text

- React Compiler is enabled.
  - Do not use `useMemo`, `useCallback`, or `React.memo`. The compiler memoizes, and
    ESLint rejects all three.
  - Use `useEffectEvent` for fresh props in stable handlers.
  - Never write refs during render.
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

- Prefer deterministic SQL tools. New LLM-grounded behavior needs three things:
  - a kill switch registered in `pipeline/flags.py`;
  - a graceful disabled path;
  - an objective stopping criterion.

## Dependencies

- Poetry is 2.x everywhere. `requires-poetry` in `pyproject.toml` makes an older
  Poetry refuse to run. CI and the Dockerfile pin one exact version inside it, which
  `tests/unit/test_poetry_pins.py` keeps identical. Run `poetry self update
  <that version>` locally.
  - Relock with the Poetry version named in `poetry.lock`'s header (Dependabot
    writes it with its own), e.g.
    `pipx run --spec 'poetry==<header version>' poetry lock`, so the format doesn't
    churn. Then run `poetry check --lock`.
  - Check what moved between groups. The deploy image installs `--only main`, so a
    package leaving the `main` group must have no production import.
- A branch that changes `pyproject.toml`/`poetry.lock` or `frontend/package*.json`
  can't be verified with the main checkout's environment. Install its own in its
  worktree first.

## Git and pull requests

- Base branch is `main`; squash merge with Conventional Commit subjects.
- Every PR gets one independent review pass over its full diff against `main` before
  it opens as a draft.
  - Claude Code runs `/review-branch` (see `CLAUDE.md`).
  - Codex uses `$review-branch`; other agents build the same prepared diff with
    `python3 scripts/prepare_review.py
    --repo <worktree> --base main --output-dir <dir>` and review it against the
    dimensions in `.claude/agents/branch-reviewer.md`.
  - Record the result and how each finding was handled in the PR body.
  - `AGENTS.md`, `CLAUDE.md`, `.claude/**`, `.agents/**`, and `.codex/**` are
    executable process docs, not ordinary prose.
- Open PRs as drafts, and mark ready only after the review pass is clean. A PR may
  then be squash-merged once all of these hold:
  - GitHub reports it mergeable/clean (no conflicts);
  - CI is green on the PR's head;
  - `main` has not advanced since the review pass ran.

  GitHub's `mergeable`/`mergeStateStatus` alone does NOT catch a `main` that moved on
  without a textual conflict. This repo has no branch-protection "must be up to date"
  rule to surface that as `BEHIND`, so check the actual SHA. A `CONFLICTING`/`DIRTY`
  state and an advanced `main` need the same fix: merge latest `main`, resolve any
  conflicts, and re-run the review pass on the result before readying or merging.
- Every PR body starts with `**Origin:** Interactive session`, as in
  `.github/PULL_REQUEST_TEMPLATE.md`.
- CI must be green on the PR's head before it merges. Branch commits carry no
  `[skip ci]`: every push to a PR runs CI, which is what the gate reads. Only the
  squash-merge commit keeps the trailer, so `main` does not re-run what the branch
  already proved.
  - `transit-app-gotchas` owns the mechanism and its traps. Chiefly, a tip that does
    carry the trailer produces no run at all, so the gate has nothing to read rather
    than something to fail.
- For stacked PRs, retarget dependants to `main` before deleting their base branch;
  GitHub otherwise closes them.
- After a PR merge, clean up persistent local/VPS clones with `make git-cleanup`
  (dry run) and then `make git-cleanup-apply`. Both use `scripts/cleanup_git_state.py`,
  whose dry run is the deletion authority, then prune the poetry venvs that no
  remaining worktree owns.
  - Only clean local worktrees (with or without a branch) may be removed, and only
    when proven recoverable from `main` or from a merged PR's permanent head ref: the
    tip is that head or an ancestor of it.
  - Remote branches, `production`, and unique post-merge commits stay.

## Process rules

- A mistake repeated twice is a missing guardrail. Capture it in the relevant skill,
  doc or check during the second occurrence.
- Promote recurring rules up the enforcement ladder: code structure → static
  analysis/tests → hooks → skills → this file. Do not keep expanding always-loaded
  prose when a deterministic check can enforce the invariant.
- Keep one canonical home per rule. Other files should point to it instead of copying
  its rationale and edge cases.
- When adding a cross-cutting concern, find every call site by searching for the
  operation itself, not for the helper. The sites that bypass the helper are the ones
  that get missed.

## Durable content only

- Comments and docstrings are not a second copy of the code. Keep them only when they
  explain one of these:
  - non-obvious rationale;
  - a durable invariant;
  - a security or data-safety boundary;
  - a public contract;
  - a nontrivial algorithm.

  Remove comments that restate the next line, describe a function's name or
  arguments, narrate routine control flow, or preserve historical phase/PR/session
  context. Prefer clear names, small functions, and tests as the durable explanation.
  During housekeeping, delete redundant prose and rewrite stale rationale instead of
  preserving it for completeness.
- Comments and docs state current, permanent facts, never who changed what or when.
  Do not cite a PR/issue number, a past bug, or a date as the reason code looks the
  way it does; state the underlying invariant directly instead. For example: "ties
  break on route_code ascending because the GROUP BY gives no ordering guarantee
  within a tie", not "fixed in PR #196". The task/PR narrative belongs in the PR
  description and commit history, not the source tree.
- Do not hardcode a measured benchmark (a specific row count, latency, or duration)
  as if it were a fixed fact. Live datasets and hardware drift, so a "measured 46.5s
  on 574M rows" comment goes stale silently and misleads the next reader. State the
  durable design rationale instead. An order-of-magnitude scale ("hundreds of
  millions of rows") is fine when it adds real intuition; a specific decaying number
  is not.
- Do not commit a markdown file that is a log of a past dev/refactor session (dated
  entries, "found X, fixed Y", slice-by-slice narrative) as permanent repo content.
  That belongs in the PR body or commit message, not a tracked file.
