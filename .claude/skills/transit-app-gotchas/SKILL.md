---
name: transit-app-gotchas
description: Non-obvious repo rules — which DB to touch, the test-DB build, i18n key parity, and the default branch. Use before running tests, writing SQL, adding UI strings, or diffing branches.
---

# transit-app gotchas

## Databases
- The raw GTFS-RT `updates` fact table (~575M rows, 4 agencies) lives in
  ClickHouse, not Postgres — migrated; the old Postgres `updates` table still
  exists as a rollback safety net but has zero production readers.
  `agg_*`/OLTP/PostGIS/pgvector stay on Postgres.
- NEVER run write SQL / migrations-down / resets against dev Postgres
  `postgresql://transit:transit@localhost:5433/transit` (wiped twice). Read-only
  verification only. Too big to clone whole — to demo on real data, slice one
  agency + a few days via read-only
  `\copy (SELECT … WHERE agency_id=… AND captured_at::date IN (…)) TO …` into a
  throwaway DB on a spare port, then migrate + analyze there.
- Same read-only rule for dev ClickHouse (`transit-ch`, ~575M real rows across 4
  agencies): no manual `INSERT`/`ALTER`/`DROP`. The one sanctioned exception is
  `make ch-bootstrap`'s documented one-time column-type `ALTER TABLE` (see
  `db/clickhouse/bootstrap.py`).
- Tests use throwaway Postgres on :5544 AND throwaway ClickHouse on :8124 —
  BOTH are required for any test touching `updates` (which is most of
  `tests/api/`, `tests/pipeline/`, `tests/query/`). Postgres image built from
  `db/` (the bare pgvector image lacks PostGIS and migration 0001 fails on
  `CREATE EXTENSION postgis`):
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

## Git
- Default branch is `main`, not master. Diff and PR against `main`.
- Squash merges; Conventional Commits subjects.
- Stacked PRs: retarget the next PR to `main` before `--delete-branch`, else GitHub
  closes (not retargets) the dependent PR.
- `git stash` is repo-wide, not worktree-scoped — a stash pushed from one
  worktree is visible (and droppable) from every other worktree and the main
  checkout. Confirmed live (2026-08-28, item 8): a freshly-dispatched VPS-loop
  worker found a prior tick's stash explicitly held for human review, reused
  its content, then ran `git stash drop` on it without authorization — an
  irreversible action (recovered only because `git gc` hadn't run yet; a
  dangling stash commit is one `git gc --prune=now` away from gone for good).
  Never run `git stash drop`/`clear`/`pop` against a stash you didn't create
  in the current session/tick; `vps-loop-run.md`'s Step 4 worker prompt now
  says this explicitly.
- `[skip ci]` must be on EVERY commit you might push as a branch's tip,
  including intermediate fix-and-reverify commits mid-branch, not just the
  first/last one. Confirmed live (2026-08-28, item 9/PR #248): 2 of 5 commits
  on a `/review-branch` fix-iteration cycle were missing the trailer, but only
  **one push** actually triggered CI — GitHub's skip-ci check is evaluated
  once per push event against that push's *tip* commit message, not
  retroactively for every individual commit in a multi-commit push. Here,
  `84a984c` (missing the trailer) and `4ed7a7b` (also missing it) were pushed
  together; since the tip (`4ed7a7b`) lacked `[skip ci]`, that one push
  triggered CI (`on: push`/`pull_request` isn't gated on the convention —
  `[skip ci]` only works because GitHub itself skips a run when it's present
  in the *triggering push's tip* commit message). The run failed in ~4s with
  "recent account payments have failed or your spending limit needs to be
  increased" — a GitHub Actions billing problem on this account, unrelated to
  the code, but real and worth knowing: a stray missing `[skip ci]` on
  whatever ends up as a push's tip is the only thing standing between "CI is
  dormant" and "CI actually runs and immediately fails for an unrelated
  reason," which could look like a real regression if not checked.
  Confirmed again live (2026-08-28, item 10/PR #250): resolving a conflict by
  running `git merge main` produces an auto-generated commit message
  ("Merge branch 'main' of ... into vps-loop/item-N") with no `[skip ci]`
  trailer — `git merge` never adds it automatically. That merge commit
  became the branch's pushed tip, so it alone (re-)triggered the same
  billing-failure CI run despite every real work commit on the branch
  correctly carrying the trailer. Always add `[skip ci]` to a merge commit
  too: either pass `git merge main -m "Merge main into vps-loop/item-N" -m
  "[skip ci]"` directly (multiple `-m` flags create a blank-line-separated
  body, avoiding a literal embedded newline in the shell string), or amend
  the default merge message before pushing.
