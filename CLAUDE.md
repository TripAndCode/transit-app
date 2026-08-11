# CLAUDE.md

Conventions for AI-assisted work in this repo. The README covers architecture; this file covers the rules that aren't derivable from code.

**Where the big picture lives** (read these before deep changes, all in README): the request path is `gtfs_pipeline.py` ingest → `pipeline/analyze.py` SQL aggs → `agg_*` tables → FastAPI routers under `api/routers/` → React SPA in `frontend/`. The Ask tab is a 3-stage router (rules → e5-small embedding NN → RAG-augmented LLM) in `pipeline/query/` — only stage 3 calls an LLM. The default request (no `time_band`/service/route filter) is served from precomputed `agg_*` tables, not live scans; a `time_band`-filtered or otherwise narrow request falls back to a live ClickHouse scan of the raw `updates` fact table. Adding a report usually means adding/extending an aggregate (see the perf-work pattern in commit history, PRs #75–79).

## Databases — read this first

- Raw GTFS-RT `updates` (~575M rows, 4 agencies) lives in **ClickHouse**, not Postgres — migrated; `agg_*`/OLTP/PostGIS/pgvector stay on Postgres. The old Postgres `updates` table still exists as a rollback safety net but has zero production readers.
- `postgresql://transit:transit@localhost:5433/transit` (the Makefile default, container `transit-pg`) is the **dev Postgres DB with real data**. Treat it as read-only: never run write SQL, migrations-down, resets, or `make db-reset`-style targets against it for testing. It has been wiped by careless test runs before. (Too large to clone whole — to demo on real data, slice one agency + a bounded date window with read-only `\copy (SELECT … WHERE …)` into a throwaway DB.)
- The dev **ClickHouse** instance (`transit-ch`, ~575M real rows) is the same rule: read-only, no manual `INSERT`/`ALTER`/`DROP`. The one sanctioned exception is `make ch-bootstrap`'s documented one-time column-type `ALTER TABLE` (see `db/clickhouse/bootstrap.py`).
- Tests use a **throwaway Postgres on :5544 AND a throwaway ClickHouse on :8124** — both are required for any test touching `updates` (most of `tests/api/`, `tests/pipeline/`, `tests/query/`). Postgres schema needs
  **PostGIS + pgvector + pg_trgm**, so build the image from `db/` — the bare
  `pgvector/pgvector:pg16` image lacks PostGIS and migration `0001` fails on
  `CREATE EXTENSION postgis`:
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
  Omitting `RUN_CH_INTEGRATION=1` does NOT fail the suite — every ClickHouse-gated test silently SKIPS instead, easy to mistake for "all passing." `make test`/`make check` don't set it either, so export the block above by hand for a run that actually covers the ClickHouse path.
- Verification against either dev DB (EXPLAIN, SELECT, API smoke tests) is fine — read-only only.

## Commands

Backend: `make serve` (FastAPI :8000), `make test` (pytest — set DATABASE_URL to :5544, see above), `make check` (fmt + lint + test), `poetry run ruff check`, `poetry run mypy`. After any fresh ingest, `make analyze` recomputes the `agg_*` tables that the default (unfiltered) read path serves from (one agency; pass `AGENCY_ID=`) — a `time_band`-filtered or otherwise narrow request instead falls back to a live ClickHouse scan, so a stale/missing `agg_*` table doesn't block those. For a full rebuild across **all** agencies use `make analyze-all` — it's fail-loud (nonzero exit if any agency fails), so a partial run can't pass silently. After a merge that changes analyze logic, run `make check-aggs` to confirm every agency's aggregates are fresh (it compares each agency's newest completed day in `updates` against `agg_route_daily` and exits nonzero if any lag). `analyze()` also stamps an audit row per agency in `agg_meta` (last-built time); it's forensic-only, nothing reads it for logic.

Focused test run — **use :5544, not :5433**. The README's single-test example (`README.md` ▸ Development) points `DATABASE_URL` at the dev DB; because the root `conftest.py` auto-migrates, that recipe runs migrations against the real-data dev DB. Safe form (add the `RUN_CH_INTEGRATION`/`CLICKHOUSE_*` block above too if the target test touches `updates`, which most of `tests/query/` does):
```bash
DATABASE_URL=postgresql://transit:transit@localhost:5544/transit_test GROQ_API_KEY=test-key \
  RUN_CH_INTEGRATION=1 CLICKHOUSE_HOST=localhost CLICKHOUSE_PORT=8124 \
  CLICKHOUSE_USER=transit CLICKHOUSE_PASSWORD=transit CLICKHOUSE_DATABASE=transit_test \
  poetry run pytest tests/query/test_tool_queries.py -k some_name -v
```
DB-free `tests/unit/` need no DATABASE_URL. Frontend single test: `cd frontend && npm run test -- MapTab` (vitest filter).

Test layout: `tests/unit/` holds **DB-free** tests — its `conftest.py` no-ops the session `apply_schema` fixture, so pure-logic tests run in <1s without Postgres. Everything else under `tests/` (and the domain subpackages `tests/api/`, `tests/query/`, `tests/scripts/`) inherits the root `conftest.py` that auto-migrates `transit_test`. Put a new pure test in `tests/unit/`; a DB/endpoint test in the matching subpackage. Tests needing the ML embedder must mock it (see `tests/scripts/test_promote_intent_cache.py`) or gate behind `@pytest.mark.slow` (`RUN_SLOW=1`).

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

- Default branch is `main` (not master); diff and open PRs against `main`.
- Squash merges to `main`; Conventional Commits subjects.
- Stacked PRs: merge bottom-up, but **don't `--delete-branch` while a dependent PR still targets that branch** — GitHub closes (not retargets) the dependent PR and it can't be reopened once its base is gone. Retarget the next PR to `main` first; delete branches at the end.
- Phase-sized features get functional reviews on live data before merge (see `review-branch.md` flow).
