# Transit Delay App

Transit Delay App turns Japanese GTFS and GTFS-Realtime feeds into delay
reports, maps, and a bilingual React dashboard. FastAPI serves the API and the
SPA from one origin. Raw realtime events live in ClickHouse; static schedules,
aggregates, and application data live in Postgres/PostGIS.

## Start Here

### Requirements

- Python 3.11+ (CI and the production image run the same minor version; see `Dockerfile`)
- [Poetry](https://python-poetry.org/) 2.5+ (`requires-poetry` in `pyproject.toml`;
  `poetry self update` upgrades an older install)
- Docker Desktop
- An LLM provider API key (Gemini by default; see `CHAT_PROVIDERS`). The API
  refuses to start without one; any non-empty value boots it, but LLM-backed
  answers need a real key.

### Local setup

```bash
cp .env.example .env
# Set GEMINI_API_KEY in .env (required to start). Other settings have local defaults.
make bootstrap
make doctor
make serve
```

Open <http://localhost:8000>. `make bootstrap` installs dependencies, starts
the databases, applies migrations, seeds agencies, and bakes the frontend into
`api/static/`. It is safe to run again.

For frontend hot reload, use two terminals:

```bash
make serve          # FastAPI on :8000
make frontend-dev   # Vite on :5173
```

The Vite server proxies API requests to FastAPI. OAuth callbacks still use
`:8000`, as configured by `PUBLIC_BASE_URL`. To sign in through Vite, set
`FRONTEND_BASE_URL=http://localhost:5173` in `.env` so the post-login redirect
returns to `:5173` instead of `:8000`.

## Load Data

The bootstrap database is empty. Choose one path:

```bash
# Fallback: load each agency's timetable, sample the current agency feeds into
# updates_live, then build every agency's aggregates. A sample reaches agg_*
# only once its service day closes and the promotion sweep (the cron poke, or
# an admin ingest run) copies it into updates.
poetry run python gtfs_pipeline.py refresh-static
poetry run python gtfs_pipeline.py ingest_live
make analyze-all

# Or replay the archives the Oracle collector still holds (the days it has not
# uploaded to R2 yet).
make fetch-ingest
```

The archive path pulls GTFS-Realtime and static GTFS archives, then runs ingest,
static loading, and aggregation. A `static_join` agency needs its timetable
loaded before it can be ingested (`REQUIRES_STATIC_TIMETABLE` in
`pipeline/strategies/__init__.py`). The live path is useful for local checks but
does not provide the same historical coverage. Add a one-off agency with:

```bash
poetry run python gtfs_pipeline.py add_agency \
  --name "My Agency" --feed-url "https://example.test/feed.pb"
```

### Fill days the dashboard is missing

The admin agencies page's 再集計 and まとめて更新 read the agency's live feed,
which carries only the present moment, so neither fills a past day. Past days
come from the collector's daily archives. The collector VM deletes each archive
once R2 holds it, so `make fetch-ingest` (and its `SINCE`) reaches only the days
not uploaded yet. `make catch-up-from-r2` catches every agency up from the
bucket instead: it ingests the days after each agency's newest row and loads
R2's newer timetables between them, so each day joins the timetable in force on
it, which a plain `make ingest` over many days does not. It prints its plan and
changes nothing until `APPLY=1`; `scripts/catch_up_from_r2.py`'s docstring has
its rules.

```bash
make catch-up-from-r2              # the plan: days, timetable loads, download size
make catch-up-from-r2 APPLY=1      # run it; then `make analyze-all`, as it prints
SINCE=20260917 make fetch-ingest   # or fetch and ingest the days still on the VM from that date on
make check-aggs                    # confirm the aggregates reach the newest day
```

Days already ingested are skipped, and the same run aggregates each newly
ingested day. After a repair that rewrites rows already stored (below), rebuild
every date with `make analyze-all FULL=1` instead.

### Repair archive-ingested rows

`scripts/repair_archive_rows.py` brings archive-ingested `updates` rows to what
archive ingest assigns them today: the `captured_at` read from each file's own
feed header, and, for `static_join` agencies, the timetable fields of rows
ingested before a timetable was loaded, so load each `static_join` agency's
current timetable first. It targets whatever `CLICKHOUSE_*` and
`DATABASE_URL` name, and prints both. Without `--apply` it only reads, and
reports per agency the rows that would move (with a histogram of the shift),
the blank rows the timetables would fill, and the disk the rebuild needs.

```bash
poetry run python scripts/repair_archive_rows.py                  # report only
poetry run python scripts/repair_archive_rows.py \
  --extra-static 8=path/to/gtfs_static_20260816.zip               # an older timetable, for retired trips
poetry run python scripts/repair_archive_rows.py --apply          # rebuild; asks to confirm
make analyze-all FULL=1 && make check-aggs                        # then rebuild every date's aggregates
```

`--apply` copies `updates` into `updates_repair` one partition at a time,
verifies the copy, and swaps it in with `EXCHANGE TABLES`. The original rows
stay in `updates_repair` until you drop it. Rerun with `--resume`, and the same
options, to finish a run that stopped partway. `--agency` limits which rows
change, not how much is rewritten: the whole table is still copied and swapped.
The script's docstring has the details.

## Data Flow

```text
GTFS-RT feeds
    |
    v
Oracle collector -- daily archives --> Cloudflare R2 (backfill only)
    |
    | pushed polls
    v
ClickHouse updates_live (today) --cron: promote closed JST days--> updates (history)
    |                                                                  |
    |                                                    analyze --> Postgres agg_*
    v                                                                  |
FastAPI + React SPA  <-------------------------------------------------+
```

- Oracle is the production collector. It pushes polls to the API, which keeps
  them in `updates_live`, and it uploads daily archives to R2.
- A daily cron poke promotes each closed JST day into `updates` (history) and
  rebuilds aggregates. The R2 archive job backfills only days the live path
  never promoted.
- Without collector streaming, `ingest_live` takes one live sample per poke.
- The API normally reads precomputed `agg_*` tables; narrow time-band queries
  may read ClickHouse directly.
- `analyze` computes the tables in `pipeline/ch_aggregates.py`'s
  `COPIED_TABLES` in ClickHouse, stores them there and copies them into
  Postgres; the whole-history ones are finalized from ClickHouse-only tables
  of delay counts and of the stop-route pairs each date observed. Those that
  join the static schedule read `analyze`'s ClickHouse copy of it,
  `static_stop_times`, refreshed when the schedule
  changes. It builds the rest in Postgres. A ClickHouse that predates one of
  those tables needs `make ch-bootstrap` before the next `analyze`.
- Each day's planned trips are saved into `planned_trips_daily` while the
  timetable that plans the day is loaded: every `analyze` run saves each day
  before today the loaded timetable covers that has no saved count yet, and
  `load_static` first saves the outgoing timetable's days before the incoming
  one starts, today and later days included. The day in progress follows the
  latest timetable loaded that day until it is over. The service-delivered
  rate reads a saved day before the loaded timetable, so a reload no longer
  drops past days from it. Nothing reconstructs the days before the first save.
- The Ask tab uses deterministic SQL tools first. Only the long-tail fallback
  calls an LLM, and optional LLM features have kill switches.

See the [deployment guide](docs/deploy-railway.md) for production setup and
the [feature guides](docs/features/) for user-facing behavior.

## Commands

| Command | Purpose |
| --- | --- |
| `make db` | Start Postgres/PostGIS and ClickHouse |
| `make db-down` | Stop databases and keep their volumes |
| `make migrate` | Apply pending Postgres migrations |
| `make load_static STATIC_PATH=./raw_archives_static` | Load static GTFS |
| `make ingest FOLDER=./raw_archives` | Load realtime archives (a `static_join` agency's timetable first) |
| `make analyze` | Rebuild one agency's changed dates (`FULL=1`: every date) |
| `make analyze-all` | Rebuild the changed dates' aggregates for all agencies (`FULL=1`: every date) |
| `make fetch-ingest` | Fetch Oracle archives and run the full local pipeline (`SINCE=YYYYMMDD` fetches only that day's RT archives and later; per-agency layout only) |
| `make catch-up-from-r2` | Plan catching every agency up from R2, timetable loads interleaved by day (`APPLY=1` runs it, `ANALYZE=1` analyzes after, `ALLOW_GAPS=1` passes a day R2 lacks and loads a timetable after days with no rows, `AGENCY="8 53"` narrows it) |
| `make check-aggs` | Detect stale aggregate tables |
| `make digest` | Generate the daily delay digest (Markdown, ja/en) |
| `make ingest-weather` | Ingest daily weather observations (kill-switched by `WEATHER_INGEST_ENABLED`) |
| `make reprobe-rt-coverage` | Re-probe each agency whose RT field-coverage verdict is missing, refuted or about to lapse; probes only in service hours, so schedule it daily in the late morning |
| `make build-rag-index` | Build the Ask RAG index for all agencies (also the re-index after an embedder change) |
| `make ask-eval` | Check the Ask gold set's stored signatures still match canonicalization (no database; pytest runs it too) |
| `make prune-query-log` | Delete `ask_query_log` rows older than 90 days (the API also runs this daily) |
| `make prune-pipeline-runs` | Delete `pipeline_runs` rows older than 90 days |
| `make prune-admin-audit` | Delete `admin_audit` rows older than 400 days (matches the deploy's data-retention horizon; the API also runs this daily) |
| `make prune-personal-data` | Delete usage counts and login history older than 25 months, and expired sessions (the API also runs this daily) |
| `make doctor` | Check environment, ports, databases, and baked SPA |
| `make hooks` | Install/verify the mandatory gitleaks pre-commit hook |
| `make verify-secrets` | On-demand gitleaks scan of the full git history |
| `make verify-secrets-all-branches` | Gitleaks scan across every branch and tag, not just HEAD |
| `make geosql-up` | Start the optional local GeoSQL/Dekart spatial-SQL tool |
| `make geosql-down` | Stop GeoSQL/Dekart |
| `make oracle-tests` | Run the Oracle collector's shell test suite |
| `make git-cleanup` | Preview stale local Git cleanup |
| `make git-cleanup-apply` | Apply safe local Git cleanup |

To remove local database data completely, use `docker compose down -v`. This is
destructive and is not part of the normal reset flow.

### GeoSQL / Dekart (optional)

`make geosql-up` starts a local [Dekart](https://dekart.xyz/) instance for
exploratory spatial SQL, local-only and never wired into `check`/`test`/
`serve`. `tools/geosql/bootstrap.sh` prints the connection string to add, derived
from `DATABASE_URL` with a read-only session; it
points at the dev Postgres/PostGIS database, so the same read-only rule as
any other dev-database access applies — see `AGENTS.md`. `make geosql-down`
stops it.

## Development

```bash
make fmt
make lint
make test
make check
```

`make test` always runs against the throwaway Postgres/ClickHouse instances on
`:5544`/`:8124` via `scripts/run_integration_tests.sh`, never the real dev
database, regardless of whatever `DATABASE_URL` the caller has configured.
That runner also exports `RUN_CH_INTEGRATION=1`, so the ClickHouse-gated tests
actually run rather than silently skipping. `make check` runs `fmt-check`
(verifies formatting, doesn't rewrite files), `lint`, `typecheck`, then `test`.

Example targeted test:

```bash
scripts/run_integration_tests.sh tests/query/test_tool_queries.py -v
```

That fixed `:5544`/`:8124` pair is shared, and a concurrent run against it can
interfere with another's schema mid-test. When a run might overlap with
another one on the same host, use `scripts/run_full_ci.sh` instead — its
header explains why and describes the isolated Postgres/ClickHouse pair it
uses instead:

```bash
scripts/run_full_ci.sh
```

Frontend checks: see `AGENTS.md`'s Verification commands section for the
full required list to run before opening a PR.

The React Compiler is enabled, and ESLint rejects `useMemo`, `useCallback`, and
`React.memo`. User-visible strings require matching `ja`
and `en` translation keys.

## API

The main endpoints are:

| Endpoint | Purpose |
| --- | --- |
| `GET /health` | Liveness check |
| `GET /api/agencies` | List agencies |
| `GET /api/{agency_id}/delays/live` | Latest realtime delays |
| `GET /api/{agency_id}/reports/{type}` | Report data or CSV |
| `GET /api/{agency_id}/delays/heatmap` | Delay heatmap data (API only; the SPA does not call it) |
| `POST /api/{agency_id}/conversations/{cid}/messages` | Deterministic Ask tools |
| `POST /api/{agency_id}/ask` | Natural-language Ask fallback |
| `GET /api/auth/{provider}/login` | Start Google or GitHub OAuth |
| `/api/admin/board`, `/api/admin/runs` | Admin control room: fleet health board + run timeline |
| `/api/admin/agencies*` | Admin agency list, per-agency health, and diagnostics drawer |
| `/api/admin/users*` | Admin user management, sessions, API keys |
| `/api/admin/audit` | Merged admin-action + login audit log |
| `/api/admin/ops` | Read-only ops health snapshot (migrations, aggregate freshness) |
| `/api/admin/flags` | Feature-flag registry: resolved values + DB overrides |
| `/api/admin/ask/*` | Ask query log, funnel, intent-cache promotion, eval result |
| `/api/admin/architecture/*` | Serves `docs/features/*.md` to the in-product architecture page |
| `/api/admin/api-keys`, `/api/admin/invites` | Issue/revoke API keys, invite new users |
| `/api/me/export` | Download everything the app holds about you (JSON) |
| `DELETE /api/me` | Delete your own account and its personal data (confirm with your email) |

Most data endpoints accept `from`, `to`, `dow`, `time_band`, `service`, and
`routes` filters. API documentation is available from FastAPI at `/docs`
while the server is running and `OPENAPI_DOCS_ENABLED` is on (see
Configuration below).

Example:

```bash
# While sign-in is required, pass a session cookie from a signed-in browser
# (or an X-API-Key header issued from the admin console).
curl -X POST http://localhost:8000/api/1/ask \
  -H 'Content-Type: application/json' \
  -H 'Origin: http://localhost:8000' \
  -H 'Cookie: sid=<your session cookie>' \
  -d '{"question":"系統5の遅延は？"}'
```

## Configuration

Copy `.env.example` and set only what your environment needs. Important groups:

- `DATABASE_URL`, `CLICKHOUSE_*`: database connections.
- `GEMINI_API_KEY`, `OPENAI_API_KEY`, `CHAT_PROVIDERS`: Ask provider ladder. At
  least one provider listed in `CHAT_PROVIDERS` needs a key, or the API will
  not start.
- `CRON_SECRET`: protects the internal live-ingest endpoint.
- `GOOGLE_CLIENT_*`, `GITHUB_CLIENT_*`, `SESSION_SIGNING_KEY`,
  `PUBLIC_BASE_URL`, `FRONTEND_BASE_URL`, `ADMIN_EMAILS`: optional
  authentication and admin setup.
- `DEFAULT_ADMIN_USERNAME`, `DEFAULT_ADMIN_PASSWORD`: break-glass local-admin
  account, seeded (and re-seeded on every boot) only when both are set.
  Rotating it is editing `.env` and restarting — the same mental model as
  rotating an OAuth client secret. Never set `DEFAULT_ADMIN_USERNAME` to a
  live SSO user's email: seeding refuses to promote an email that already
  belongs to a real OAuth-linked account, so a collision just logs an error
  and leaves that account untouched.
- `OPS_STATUS_REPO`: the git checkout the admin board's `github` collector
  reads, when it differs from its built-in default path. Unset
  on a host where that path doesn't exist and the board's `github` tile
  reads `unknown` rather than a real status.
- `OBJECT_STORE_*`, `AGENCY_IDS`, `RETENTION_DAYS`: scheduled archive ingest.

### Feature kill switches

Each key can be overridden from the admin control room's flags page;
`pipeline/flags.py` owns the resolution and caching rules. "Default" is the
value when neither an override nor the env var is set.

| Key | Env var | Default |
| --- | --- | --- |
| `ask_router_enabled` | `ASK_ROUTER_ENABLED` | on |
| `ask_llm_enabled` | `ASK_LLM_ENABLED` | on |
| `ask_followup_enabled` | `ASK_FOLLOWUP_ENABLED` | off |
| `copilot_insight_enabled` | `COPILOT_INSIGHT_ENABLED` | off |
| `ask_history_enabled` | `ASK_HISTORY_ENABLED` | on |
| `ask_intent_cache_enabled` | `ASK_INTENT_CACHE_ENABLED` | off |
| `ask_query_log_enabled` | `ASK_QUERY_LOG_ENABLED` | on |
| `weather_ingest_enabled` | `WEATHER_INGEST_ENABLED` | off |
| `openapi_docs_enabled` | `OPENAPI_DOCS_ENABLED` | off |
| `perf_debug_enabled` | `PERF_DEBUG_ENABLED` | off |
| `login_required` | `LOGIN_REQUIRED` | on |
| `ml_forecast` | `ML_FORECAST_ENABLED` | off |

`openapi_docs_enabled` gates `/docs`, `/redoc`, and `/openapi.json`;
`.env.example` turns it on for local dev.

`login_required` makes every `/api/*` route and the OpenAPI docs refuse a
signed-out caller with `401 {"detail": "auth required"}`, except the sign-in
flow (`/api/auth/*`) and `/api/config`. It is enforced only while SSO is
configured.

`ml_forecast` lets `GET /api/{agency_id}/forecast/heatmap` and
`/forecast/overview` fill a cell from the ML model's fresh forecasts in
`agg_route_forecast_ml`, marked `source: "ml"`; with it off, every cell is the
historical baseline.

Leaving all OAuth variables unset runs the app in anonymous-only mode: nobody
can sign in through SSO (only the break-glass account, when it is set), so the
login gate stays open and every page works signed out. Do not
commit `.env`, API keys, OAuth secrets, database passwords, or private keys.

### Secret scanning

`make bootstrap` installs a mandatory gitleaks pre-commit hook (see
`.pre-commit-config.yaml`) that scans every commit's staged content before it's
created; bootstrap fails if the hook can't be installed. Re-run `make hooks` on
its own after a clean checkout, a new machine, or a VPS clone — worktrees of the
same clone share one `.git/hooks` directory, so one run covers all of them.
`make doctor` reports whether the hook is currently installed. `make
verify-secrets` runs the same scanner against the full git history on demand,
and CI's `secrets-scan.yml` runs it again on every pull request and push to
`main` as a backstop for commits made without the hook installed.

## Deployment

Production uses Railway services for the app, Postgres, and the scheduled
ingest job. The Docker image builds the React SPA and serves it from FastAPI.
The database remains on Railway's private network, and migrations run through
the deployment configuration.

Read [docs/deploy-railway.md](docs/deploy-railway.md) before deploying. It
covers service creation, variables, data loading, custom domains, backups, and
the Oracle-to-R2 archive path.

## Repository Map

```text
api/                    FastAPI app, auth, routers, middleware
pipeline/               ingest, static loading, aggregation, reports, Ask
db/                     Postgres/PostGIS and ClickHouse schemas
deploy/                 systemd units and log rotation for ops monitoring and drift checks
frontend/               React SPA and translations
oracle_cloud/           Oracle VM collector agent (v3): archive fetch, R2 sync, health checks, alerting
scripts/                operational tools and review helpers
tests/                  pytest suites (api, pipeline, query, db, frontend, unit) and fixtures
tools/                  optional local dev tools (GeoSQL/Dekart)
docs/features/          feature-specific behavior guides
.claude/                Claude Code workflows and shared skill instructions
.agents/                Codex skills, including links to shared skill instructions
.codex/                 local Codex hook configuration
AGENTS.md               shared coding-agent rules (CLAUDE.md imports them)
```

Useful entry points:

- `gtfs_pipeline.py`: command-line pipeline entry point.
- `pipeline/ingest.py`: GTFS-Realtime parsing.
- `pipeline/static_loader.py`: static GTFS loading.
- `pipeline/analyze.py`: aggregate rebuilding.
- `api/main.py`: FastAPI application.
- `frontend/src/`: React application.

## Safety Rules

- Dev databases are read-only; see `AGENTS.md`. Use the throwaway `:5544`/
  `:8124` pair described above for writes.
- Never push directly to `main`; use reviewed squash-merged PRs.
- Branch commits carry no `[skip ci]`, so every push to a PR runs CI and the
  merge gate has a result to read. Only the squash-merge commit carries the
  trailer, keeping `main` from re-running what the branch proved. See
  `AGENTS.md` for the rule and `transit-app-gotchas` for how it behaves.
- Run the relevant checks before opening a PR, then run `make check` when the
  change affects backend behavior.
