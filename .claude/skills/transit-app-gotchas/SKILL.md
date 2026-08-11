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
