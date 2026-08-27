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
- eslint `react-hooks` compiler rules: `set-state-in-effect` and `purity` are enforced as `error` project-wide (eslint-plugin-react-hooks v7's `recommended-latest` default — this repo currently has zero violations, so there's no legacy-code carve-out in place or needed). Prefer derived state over sync-effects.
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
- **Every PR gets a `/review-branch` pass before it's opened — no exceptions for small/trivial diffs, and this applies identically whether the work originated from an interactive session or the autonomous VPS loop.** The trivial-tier fast path exists for genuinely doc-only diffs; it is not a reason to skip the flow entirely.
- Every PR body states its origin: `**Origin:** Interactive session` or `**Origin:** Autonomous VPS loop (item N)` (see `pr-github.md`'s PR description style).
- CI is intentionally skipped on every commit right now (add `[skip ci]` on its own line/trailer to every commit message, including ordinary code changes) — rely on the local checks below (and the pre-push hook) instead of GitHub Actions.

## Process

- **Capture repeated mistakes immediately.** If an agent (autonomous loop or interactive session) makes the same mistake twice in this repo, don't just fix it and move on — capture the rule in `transit-app-gotchas` (or whichever skill already covers that area) in the *same session* as the second occurrence, not deferred to a follow-up task. A mistake seen once is noise; seen twice, it's a gap in the documented rules that will keep costing time until it's written down somewhere an agent actually reads before acting.
- **Enforcement ladder.** Rules can live at several layers, roughly from strongest/automatic to weakest/advisory: codebase (the code itself makes the mistake impossible, e.g. a type or a guard clause) → static analysis/CI (lint, `mypy`, tests) → bot rules (pre-commit/pre-push hooks, PR-bot checks) → skills (on-demand docs like `transit-app-gotchas`, loaded when relevant) → style guide (this file — read-only guidance, relies on whoever's working remembering to check it). When the same rule gets flagged in review 2+ times despite already being documented, that's a signal it's sitting at too weak a layer for how often it's violated — promote it up the ladder (e.g. skill → hook, or hook → CI check) instead of just re-documenting it again in the same place.

## Autonomous VPS loop

A Claude Code CLI instance runs unattended on a dedicated VPS (separate from any dev machine), driven by cron, to advance work incrementally without a human keeping a session open.

- **Where**: a small VPS clone of this repo (not the primary dev machine), authenticated to GitHub via a personal token (`gh auth login`) and to Anthropic via `claude setup-token` (a long-lived OAuth token, not a metered API key).
- **Gotcha: non-interactive shells don't source `~/.bashrc`.** `ssh host 'cmd'` and cron both invoke a non-interactive, non-login shell, which skips `~/.bashrc` — this bit setup twice (`CLAUDE_CODE_OAUTH_TOKEN` and the `poetry` `PATH` entry both silently failed to apply until traced back to this). Put anything the loop needs system-wide in `/etc/environment` (plain `KEY=VALUE`, no `export`) or symlink the binary into `/usr/local/bin`, rather than relying on `~/.bashrc`.
- **Cadence**: a system cron job runs a fixed wrapper script hourly (off the round-minute mark) that invokes `claude -p` non-interactively with a fixed meta-prompt, then exits — it does not stay resident between runs.
- **`NEXT_TASK.md`** (repo root, untracked/local — not meant to be committed as part of normal feature work) is the loop's only input: freeform markdown describing the current task, a refactor backlog (candidates found but not yet started — split into small steps before starting any), and a status log the loop appends to after each run. Empty or missing file → the run is a safe no-op ("standing by"). There is no other entry point — to hand it a new task, either write it locally and push it to the VPS:
  ```bash
  scp -i ~/.ssh/conoha/<key>.pem ./NEXT_TASK.md root@<vps-ip>:/root/transit-app/NEXT_TASK.md
  ```
  or edit the file on the VPS directly:
  ```bash
  ssh -i ~/.ssh/conoha/<key>.pem root@<vps-ip>
  nano /root/transit-app/NEXT_TASK.md
  ```
  Either way, the change takes effect on the loop's next hourly run — there's no way to trigger it early short of SSHing in and running `/root/claude-loop.sh` manually.
- **Per run**: read `NEXT_TASK.md` → advance by one incremental step only (never attempt the whole task in one run) → run the relevant tests → commit (with `[skip ci]`, per above) → push a feature branch and open/update a PR via `gh` → update the status log. Every commit still goes through review like any other PR; the loop never merges its own work.
- **Safety layering**: the shared, tracked `.claude/settings.json` hooks (`guard-dev-db.sh` blocking write/DDL SQL against the dev DB on :5433, `guard-push-quality.sh` gating `git push` on lint/tests) apply here same as anywhere. On top of that, the VPS checkout has its own `.claude/settings.local.json` (gitignored, VPS-only — never commit this) that allowlists the narrow set of safe operations the unattended loop needs (reads, the test/lint commands, `git`/`gh` up through opening a PR) and explicitly denies `git push` to `main`, force-push, `reset --hard`, `rm -rf`, and DB-reset targets. The loop can never push to `main` directly or merge — only open PRs for a human to review.
- **Known limitation**: `guard-push-quality.sh`'s backend-test timeout (240s) was tuned on faster hardware than the current VPS plan (4 vCPU/4GB), where a full `pytest` run takes ~6.5 minutes — a clean push can still fail the gate on timeout alone. Not yet resolved; if pushes start failing consistently with no real test failure in the log, that's the likely cause.
