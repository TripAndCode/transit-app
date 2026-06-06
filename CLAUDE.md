# CLAUDE.md

Conventions for AI-assisted work in this repo. The README covers architecture; this file covers the rules that aren't derivable from code.

## Databases — read this first

- `postgresql://transit:transit@localhost:5433/transit` (the Makefile default, container `transit-pg`) is the **dev DB with ~1.8M rows of real ingested data**. Treat it as read-only: never run write SQL, migrations-down, resets, or `make db-reset`-style targets against it for testing. It has been wiped by careless test runs before.
- Tests use a **throwaway Postgres on :5544** instead:
  ```bash
  docker run -d --rm --name transit-test-pg -e POSTGRES_USER=transit \
    -e POSTGRES_PASSWORD=transit -e POSTGRES_DB=transit_test \
    -p 5544:5432 pgvector/pgvector:pg16
  DATABASE_URL=postgresql://transit:transit@localhost:5544/transit_test poetry run pytest
  ```
- Verification against the dev DB (EXPLAIN, SELECT, API smoke tests) is fine — read-only only.

## Commands

Backend: `make serve` (FastAPI :8000), `make test` (pytest — set DATABASE_URL to :5544, see above), `poetry run ruff check`, `poetry run mypy`.

Frontend (`cd frontend`): `npm run typecheck && npm run test && npm run lint && npm run lint:i18n && npm run lint:i18n-strings` — all five must pass before a PR. `npm run dev` for the hot-reload loop (proxies `/api` to :8000).

## Frontend rules

- React 19.2 with the **React Compiler enabled** — do not add `useMemo`/`useCallback`/`React.memo` for performance. For fresh-props-in-stable-handlers, use `useEffectEvent` (see `MapTab`), never render-time ref writes.
- eslint `react-hooks` compiler rules: `set-state-in-effect` and `purity` are `warn` only for pre-existing code. New code keeps them clean — prefer derived state over sync-effects.
- No hardcoded UI strings: every user-visible string goes through `t()` with keys in **both** `frontend/src/i18n/locales/{ja,en}.json` (key parity is CI-linted). Kana in `.ts/.tsx` source fails `lint:i18n-strings`; suppress intentional cases with `i18n-ignore`.
- Calm UI: no alarm reds, no dense panels, no stressful motion. Severity uses the existing warm ramp.
- New routes/pages must be `React.lazy` like the existing ones; keep MapLibre out of the entry chunk.

## LLM features

- Primary Ask path is deterministic SQL tools — no LLM. Anything LLM-grounded ships behind an env kill switch with a graceful disable path (pattern: `ASK_FOLLOWUP_ENABLED`), and needs an objective stop criterion defined up front.
- Server-side user-facing strings live in the `_LOCALES` table in `pipeline/query/tools.py` (ja + en per key); tests pin exact strings, so update both together.

## Git / PRs

- Squash merges to `main`; Conventional Commits subjects.
- Stacked PRs: merge bottom-up, but **don't `--delete-branch` while a dependent PR still targets that branch** — GitHub closes (not retargets) the dependent PR and it can't be reopened once its base is gone. Retarget the next PR to `main` first; delete branches at the end.
- Phase-sized features get functional reviews on live data before merge (see `review-branch.md` flow).
