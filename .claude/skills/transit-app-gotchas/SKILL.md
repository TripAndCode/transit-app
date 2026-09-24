---
name: transit-app-gotchas
description: Non-obvious repo rules — which DB to touch, the test-DB build, i18n key parity, and the default branch. Use before running tests, writing SQL, adding UI strings, or diffing branches.
---

# transit-app gotchas

## Databases
- The raw GTFS-RT `updates` fact table (hundreds of millions of rows across 4
  agencies, and growing) lives in ClickHouse, not Postgres — migrated; the old
  Postgres `updates` table still exists as a rollback safety net but has zero
  production readers.
  `agg_*`/OLTP/PostGIS/pgvector stay on Postgres.
- Dev Postgres read-only rule (`postgresql://transit:transit@localhost:5433/transit`,
  wiped twice): canonical in `CLAUDE.md`. Too big to clone whole — to demo on
  real data, slice one agency + a few days via read-only
  `\copy (SELECT … WHERE agency_id=… AND captured_at::date IN (…)) TO …` into a
  throwaway DB on a spare port, then migrate + analyze there.
- Same rule applies to dev ClickHouse (`docker compose exec clickhouse`,
  hundreds of millions of real rows across 4 agencies). The one sanctioned
  exception is
  `make ch-bootstrap`'s documented one-time column-type `ALTER TABLE` (see
  `db/clickhouse/bootstrap.py`).
- Tests use throwaway Postgres on :5544 AND throwaway ClickHouse on :8124 —
  BOTH are required for any test touching `updates` (which is most of
  `tests/api/`, `tests/pipeline/`, `tests/query/`). Postgres image built from
  `db/`, which layers PostGIS and pgvector onto the official multi-architecture
  `postgres` base — a stock `postgres` or bare pgvector image lacks PostGIS and
  migration 0001 fails on `CREATE EXTENSION postgis`. It builds natively on
  amd64 and arm64 alike; if `docker` reports a platform mismatch for this
  container, something is forcing an architecture and every query will pay an
  emulation tax large enough to push the full suite past the pre-push gate's
  timeout:
  ```bash
  docker run -d --rm --name transit-test-pg -e POSTGRES_USER=transit \
    -e POSTGRES_PASSWORD=transit -e POSTGRES_DB=transit_test \
    -p 5544:5432 "$(docker build -q db/)"
  make ch-test   # throwaway ClickHouse on :8124, matches CI's pinned 26.8
  DATABASE_URL=postgresql://transit:transit@localhost:5544/transit_test \
    RUN_CH_INTEGRATION=1 CLICKHOUSE_HOST=localhost CLICKHOUSE_PORT=8124 \
    CLICKHOUSE_USER=transit CLICKHOUSE_PASSWORD=transit CLICKHOUSE_DATABASE=transit_test \
    poetry run pytest
  ```
  Omitting `RUN_CH_INTEGRATION=1` doesn't fail the suite — it silently SKIPS
  every ClickHouse-gated test instead, which is easy to mistake for "all
  passing." `make test`/`make check` are covered: both go through
  `scripts/run_integration_tests.sh`, which exports it. A bare `poetry run
  pytest` is not — export the block above by hand for that.
- The `transit-test-pg`/`transit-test-ch` pair above is a fixed name on a
  fixed port, and this repo also keeps a long-lived instance of it running
  for everyday local use. Two runs against that same pair at once — e.g. an
  interactive verification pass and a concurrent `/vps-loop-run` worker's, in
  two different worktrees on the same VPS — can race and produce spurious
  failures unrelated to either diff. For any run that might overlap with
  another one on the same host, use `scripts/run_full_ci.sh` instead — its
  header is canonical on why and how it avoids the shared pair.
  `scripts/run_integration_tests.sh` itself also accepts `TEST_PG_PORT`/
  `TEST_CH_PORT` overrides (defaulting to the shared `:5544`/`:8124` pair)
  for a caller that starts its own containers by some other means.
- `run_full_ci.sh` does NOT measure coverage by default, and neither does
  the CI run that gates a PR: `ci.yml` measures it on `main` only, since
  nothing gates on the number. Pass `COVERAGE=1` when the number itself is
  what you want. The instrumentation is minutes per run on a VPS sharing
  CPU with a concurrent `/vps-loop-run` tick, and this gate is often paid
  more than once per branch.

## Frontend dev proxy — two config files
- `frontend/` ships BOTH `vite.config.ts` (tracked) and a gitignored
  `vite.config.js` — **vite reads the `.js`**. Editing only the `.ts` silently
  no-ops the dev proxy. Change both (or the `.js`) when repointing `/api`.

## Frontend i18n
- Every user-visible string goes through `t()` with keys in BOTH
  `frontend/src/i18n/locales/{ja,en}.json` (key parity is CI-linted).
- Kana in `.ts/.tsx` source fails `lint:i18n-strings`; suppress intentional cases
  with `i18n-ignore`.
- The full pre-PR check list is canonical in `CLAUDE.md`'s Verification
  commands section — run it before opening a PR.

## VPS loop / sandboxed worker sessions
- A dispatched VPS-loop worker's sandbox has NO `poetry install`/`npm
  install` permission and often no already-provisioned virtualenv/
  `node_modules` package at all. Symptoms: `poetry run pytest`/`ruff`/`mypy`
  report "Command not found", a worktree's poetry virtualenv has zero
  installed packages (`poetry env info` there reports `Path: NA`), and/or
  `frontend/node_modules` is missing a newly-added dependency because `npm
  install` was never actually run for that worktree. A dispatched worker
  cannot close this itself — every workaround it can reach (hand-tracing
  logic against source instead of running it, vendoring a stub package into
  `node_modules`) is insufficient.
- **`node_modules` sharing is NOT guaranteed — verify before relying on
  it.** Git worktrees do NOT share gitignored/untracked directories
  automatically: `frontend/node_modules` is normally a plain directory (not
  a symlink) in both the main checkout and any freshly-created worktree, so
  an `npm install` run in one worktree does not cover another. Check with
  `ls -la frontend/node_modules` (or `python3 -c "import os;
  print(os.path.islink('frontend/node_modules'))"`) in the SPECIFIC
  worktree you're fixing before assuming an `npm install` elsewhere already
  covers it — if it's a plain directory, you must run `npm install`
  separately in that worktree's own `frontend/`. If a symlink happens to
  exist, treat it as a possibly-deliberate, worktree-specific setup detail,
  not a repo-wide guarantee to rely on going forward.
- The same cwd-keyed resolution bites a *test or script that shells out*:
  a child invoked as `poetry run python ...` re-resolves the virtualenv from
  wherever it runs, so a suite launched from a worktree hands its subprocess a
  different, unprovisioned environment and the test fails with
  `ModuleNotFoundError` no matter what the code under test does. Pass the
  running interpreter explicitly instead — `sys.executable` from Python, or an
  interpreter-override env var for a bash wrapper — rather than letting the
  child resolve poetry itself. Watch for the failure that *passes*: a test
  asserting only a non-zero exit code is satisfied by the crash and silently
  stops checking its actual subject.
- **What actually works**: an interactive session (not a dispatched
  worker) usually has broader Bash permissions and CAN run `poetry
  install`/`npm install` for real, closing the gap after the fact. Fetch
  the worker's branch locally (or SSH into the VPS and use its own worktree
  directly), run `poetry run <ruff|mypy|pytest> <paths>` **from the main
  checkout's cwd** pointed at the worktree's file paths (poetry resolves
  its virtualenv by cwd identity, not by the file arguments — running
  `poetry run` from inside a worktree can resolve to a *different*,
  unprovisioned virtualenv even though `pyproject.toml` looks identical) —
  but if that worktree's own branch changed `pyproject.toml`/`poetry.lock`
  (added/bumped a dependency), the main checkout's venv won't have it
  either, and a resulting "module not found" is a real dependency gap to
  close, not a false alarm to explain away. Run `npm install` directly in
  whichever `frontend/` actually needs it (see the sharing caveat above —
  don't assume one `npm install` covers every worktree). For a
  Playwright/real-browser e2e test, also: build the SPA (`npm run build`)
  and bake it (`make bake`) **inside the specific worktree being tested**
  (`api/static` is untracked/gitignored per-worktree, not shared via git),
  install Chromium (`poetry run playwright install --with-deps chromium`),
  and set a dummy `GEMINI_API_KEY` if the app's startup `lifespan` requires
  one but the test itself never reaches the Ask/LLM code path.
- **Concurrency risk**: an interactive session fixing a worker's worktree
  can race with the autonomous loop's own next tick resuming the same
  branch (Step 3b's "has commits: resume and ship it yourself" path does
  not know an interactive session is also live). A coordinator tick and an
  interactive session can edit the exact same files in the exact same
  worktree within minutes of each other; the coordinator is designed to
  detect a concurrent edit and back off without committing (per its own
  "don't act on state you don't clearly own" boundary) — but a `git add -A
  && git commit` from the other side can still silently absorb the other
  actor's uncommitted edit into its own commit. Diff the resulting commit
  against what you think you wrote before trusting it; don't assume a
  clean commit only contains your own changes.

## Git
- Default branch is `main`, not master. Diff and PR against `main`. Merge/PR
  policy (squash merge, Conventional Commit subjects, stacked-PR retargeting)
  is canonical in `CLAUDE.md`'s "Git and pull requests" section — mechanically,
  retarget the next PR to `main` before `--delete-branch`, else GitHub closes
  (not retargets) the dependent PR.
- `git stash` is repo-wide, not worktree-scoped — a stash pushed from one
  worktree is visible (and droppable) from every other worktree and the main
  checkout. A freshly-dispatched VPS-loop worker finding a prior tick's
  stash explicitly held for human review, reusing its content, then running
  `git stash drop` on it without authorization can be irreversible: a
  dropped stash is only reachable until `git gc --prune=now` runs, which
  eventually will. Never run `git stash
  drop`/`clear`/`pop` against a stash you didn't create in the current
  session/tick; `vps-loop-run.md`'s Step 4 worker prompt says this
  explicitly.
- Whether a push runs CI is decided by ONE commit: the tip of that push.
  GitHub evaluates the skip trailer once per push event against that
  message alone — not retroactively across the push's other commits — so a
  multi-commit push whose tip omits it runs CI however many of the earlier
  commits carry it, and a trailer-less commit buried mid-branch runs
  nothing (`on: push`/`pull_request` is not gated on the convention; the
  trailer is the whole mechanism). The match is a plain substring anywhere
  in the message, quoting included, so a message that merely mentions the
  trailer suppresses itself.
  Root `CLAUDE.md` owns the policy this serves — when CI has to run and
  when it must be green. This entry is only the mechanism, which is easy to
  get wrong in either direction.
  Under that policy branch commits carry no trailer, so the usual direction
  of the mistake is one slipping in: a tip that carries it produces no run,
  and the merge gate then has nothing to read rather than something to
  fail — which looks like a stuck queue, not a mistake. Amend and
  `push --force-with-lease`; an empty follow-up commit works too but leaves
  the confusing commit in history. Only the squash-merge `--body` should
  contain the trailer.
