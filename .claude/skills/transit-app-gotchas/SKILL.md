---
name: transit-app-gotchas
description: Non-obvious repo rules — which DB to touch, the test-DB build, i18n key parity, and the default branch. Use before running tests, writing SQL, adding UI strings, or diffing branches.
---

# transit-app gotchas

## Databases
- NEVER run write SQL / migrations-down / resets against dev DB
  `postgresql://transit:transit@localhost:5433/transit` (~34M real `updates` rows /
  ~11GB as of 2026-06, wiped twice). Read-only verification only. Too big to clone
  whole — to demo on real data, slice one agency + a few days via read-only
  `\copy (SELECT … WHERE agency_id=… AND captured_at::date IN (…)) TO …` into a
  throwaway DB on a spare port, then migrate + analyze there.
- Tests use throwaway Postgres on :5544, image built from `db/` (the bare
  pgvector image lacks PostGIS and migration 0001 fails on `CREATE EXTENSION postgis`):
  ```bash
  docker run -d --rm --name transit-test-pg -e POSTGRES_USER=transit \
    -e POSTGRES_PASSWORD=transit -e POSTGRES_DB=transit_test \
    -p 5544:5432 "$(docker build -q db/)"
  DATABASE_URL=postgresql://transit:transit@localhost:5544/transit_test poetry run pytest
  ```

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
