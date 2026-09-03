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
- NEVER run write SQL / migrations-down / resets against dev Postgres
  `postgresql://transit:transit@localhost:5433/transit` (wiped twice). Read-only
  verification only. Too big to clone whole — to demo on real data, slice one
  agency + a few days via read-only
  `\copy (SELECT … WHERE agency_id=… AND captured_at::date IN (…)) TO …` into a
  throwaway DB on a spare port, then migrate + analyze there.
- Same read-only rule for dev ClickHouse (`transit-ch`, hundreds of millions
  of real rows across 4 agencies): no manual `INSERT`/`ALTER`/`DROP`. The one
  sanctioned exception is
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
  make ch-test   # throwaway ClickHouse on :8124, matches CI's pinned 26.3
  DATABASE_URL=postgresql://transit:transit@localhost:5544/transit_test \
    RUN_CH_INTEGRATION=1 CLICKHOUSE_HOST=localhost CLICKHOUSE_PORT=8124 \
    CLICKHOUSE_USER=transit CLICKHOUSE_PASSWORD=transit CLICKHOUSE_DATABASE=transit_test \
    poetry run pytest
  ```
  Omitting `RUN_CH_INTEGRATION=1` doesn't fail the suite — it silently SKIPS
  every ClickHouse-gated test instead, which is easy to mistake for "all
  passing." `make test`/`make check` do NOT set it, so the Makefile's own
  default local gate has this gap too; always export the block above by hand
  for a run that actually covers the ClickHouse path.

## Frontend dev proxy — two config files
- `frontend/` ships BOTH `vite.config.ts` (tracked) and a gitignored
  `vite.config.js` — **vite reads the `.js`**. Editing only the `.ts` silently
  no-ops the dev proxy. Change both (or the `.js`) when repointing `/api`.

## Frontend i18n
- Every user-visible string goes through `t()` with keys in BOTH
  `frontend/src/i18n/locales/{ja,en}.json` (key parity is CI-linted).
- Kana in `.ts/.tsx` source fails `lint:i18n-strings`; suppress intentional cases
  with `i18n-ignore`.
- 5 checks must pass before PR: `npm run typecheck && npm run test && npm run lint
  && npm run lint:i18n && npm run lint:i18n-strings`.

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
  and set a dummy `GROQ_API_KEY` if the app's startup `lifespan` requires
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
- Default branch is `main`, not master. Diff and PR against `main`.
- Squash merges; Conventional Commits subjects.
- Stacked PRs: retarget the next PR to `main` before `--delete-branch`, else GitHub
  closes (not retargets) the dependent PR.
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
- `[skip ci]` must be on EVERY commit you might push as a branch's tip,
  including intermediate fix-and-reverify commits mid-branch, not just the
  first/last one. A multi-commit push where only some commits carry the
  trailer can still trigger CI — GitHub's skip-ci check is evaluated
  once per push event against that push's *tip* commit message, not
  retroactively for every individual commit in a multi-commit push: if a
  push's tip commit lacks the trailer, that push triggers CI regardless of
  whether every other commit in it correctly has one (`on: push`/
  `pull_request` isn't gated on the convention — `[skip ci]` only works
  because GitHub itself skips a run when it's present in the *triggering
  push's tip* commit message). A stray missing `[skip ci]` on whatever
  ends up as a push's tip is the only thing standing between "CI is
  dormant" and "CI actually runs," which could look like a real regression
  if not checked, or fail for an unrelated infrastructure reason (e.g. a
  billing/quota issue) that has nothing to do with the code.
  The same gap shows up when resolving a conflict: running `git merge main`
  produces an auto-generated commit message
  ("Merge branch 'main' of ... into vps-loop/item-N") with no `[skip ci]`
  trailer — `git merge` never adds it automatically. That merge commit
  becomes the branch's pushed tip, so it alone (re-)triggers CI despite
  every real work commit on the branch correctly carrying the trailer.
  Always add `[skip ci]` to a merge commit too: either pass `git merge main
  -m "Merge main into vps-loop/item-N" -m "[skip ci]"` directly (multiple
  `-m` flags create a blank-line-separated body, avoiding a literal
  embedded newline in the shell string), or amend the default merge
  message before pushing.
