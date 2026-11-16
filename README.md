# Transit Delay App

Transit Delay App turns Japanese GTFS and GTFS-Realtime feeds into delay
reports, maps, and a bilingual React dashboard. FastAPI serves the API and the
SPA from one origin. Raw realtime events live in ClickHouse; static schedules,
aggregates, and application data live in Postgres/PostGIS.

It is an independent service-quality audit instrument for the transport-policy
staff who prepare 地域公共交通協議会 material; [Audience and job](docs/features/audience.md)
says who it is for and what each screen answers. The
[methodology and publication policy](docs/methodology.md) ([English](docs/methodology.en.md))
says how every figure is computed, what is never computed, and how an operator hears
of its figures before they are published.

## Start Here

### Requirements

- Python 3.11+ (CI and the production image run the same minor version; see `Dockerfile`)
- [Poetry](https://python-poetry.org/) 2.5+ (`requires-poetry` in `pyproject.toml`;
  `poetry self update` upgrades an older install)
- Docker Desktop
- Optional: an LLM provider API key (Gemini by default; see `CHAT_PROVIDERS`).
  The API starts without one and serves the deterministic Ask notebook;
  LLM-backed answers and follow-ups stay off until a real key is set.

### Local setup

```bash
cp .env.example .env
# Optionally set GEMINI_API_KEY in .env for LLM-backed Ask. Other settings have local defaults.
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

History and live data each have one command:

```bash
make catch-up-from-r2 APPLY=1 ANALYZE=1               # history: closed days from the R2 archive
make catch-up-from-r2 SEED_DAYS=3 APPLY=1 ANALYZE=1   # the same, for an empty database: its last 3 days
poetry run python gtfs_pipeline.py ingest_live        # live: a sample of each agency's feed into updates_live
```

**History** comes from the collector's daily archives in R2 (the four
`OBJECT_STORE_*` variables in `.env`; `make doctor` reports them). The catch-up
ingests each agency's archived days after its newest row in `updates`, loading
R2's newer timetables between them so each day joins the timetable in force on
it, then builds the aggregates. Without `APPLY=1` it prints the plan (days,
timetable loads, download size) and changes nothing; `scripts/catch_up_from_r2.py`'s
docstring has its rules. The bootstrap database is empty, so the first run
needs `SEED_DAYS`. `AGENCY="1 9"` narrows it to some agencies. An agency the plan marks `REFUSED` stops the whole apply, so leave it
out with `AGENCY=` or follow its refusal first. `make check-aggs` then confirms
the aggregates reach the newest day.

**Live** data goes to `updates_live`: the collector's pushes in production,
`ingest_live` locally (load each agency's timetable first with
`gtfs_pipeline.py refresh-static`). It serves today's screens. Once a day
closes, promotion (the cron poke, or an admin ingest run) copies it into
`updates`, and the next analyze aggregates it. That copy holds only the
polls the collector pushed, so an archive ingest that reads the day's complete
archive later replaces it (`pipeline/archive_replace.py` defines complete).
`make check-aggs` lists the days still held from the live copy. The catch-up
starts after the newest row, so it does not reach back to a promoted day: ingest
a folder holding both of that day's tarballs with
`make ingest FOLDER=<dir> AGENCY_ID=<id>`, then `make analyze-all`. For a
`static_join` agency whose timetable changed since that day, load the timetable
in force on it first, or its rows join the newer one.

Add a one-off agency with:

```bash
poetry run python gtfs_pipeline.py add_agency \
  --name "My Agency" --feed-url "https://example.test/feed.pb"
```

### Disaster tools

These rewrite rows `updates` already holds, across the whole table. Normal
operation never needs them. Run one only when stored rows are known to be wrong,
and follow it with `make analyze-all FULL=1 && make check-aggs`.

- `scripts/repair_archive_rows.py` brings archive-ingested rows to what archive
  ingest assigns them today: the `captured_at` read from each file's own feed
  header, and, for `static_join` agencies, the timetable fields of rows ingested
  before a timetable was loaded, so load each `static_join` agency's current
  timetable first. It targets whatever `CLICKHOUSE_*` and `DATABASE_URL` name,
  and prints both. Without `--apply` it only reads, and reports per agency the
  rows that would move, the blank rows the timetables would fill, and the disk
  the rebuild needs. `--apply` copies `updates` into `updates_repair` one
  partition at a time, verifies the copy, and swaps it in with `EXCHANGE TABLES`;
  the original rows stay in `updates_repair` until you drop it. Rerun with
  `--resume`, and the same options, to finish a run that stopped partway.
  `--extra-static 8=path/to/gtfs_static_20260816.zip` adds an older timetable for
  retired trips. The script's docstring has the details.
- `gtfs_pipeline.py restamp_archive` restamps archive-ingested rows with the time
  archive ingest assigns today. It prints the plan and writes nothing unless
  `--apply`.

`make fetch-ingest` replays the archives the Oracle collector still holds, the
days it has not uploaded to R2 yet. It needs SSH access to the collector VM.

## Data Flow

```text
GTFS-RT feeds
    |
    v
Oracle collector -- daily archives --> Cloudflare R2 (history)
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
  rebuilds aggregates. An archive ingest that reads a day's complete archive
  later replaces that promoted copy, which holds only the pushed polls.
- Without collector streaming, `ingest_live` takes one live sample per poke.
- The API normally reads precomputed `agg_*` tables; narrow time-band queries
  may read ClickHouse directly.
- `analyze` computes the tables in `pipeline/ch_aggregates.py`'s
  `COPIED_TABLES` in ClickHouse, stores them there and copies them into
  Postgres; the whole-history ones are finalized from ClickHouse-only tables
  of delay counts and of the stop-route pairs each date observed. Each row is
  read as joined to the timetable in force on its service date
  (`pipeline/timetable_in_force.py`): the newest loaded timetable whose feed
  dates, or else calendar, cover that day. A row ingested against another
  timetable, or against none, takes the in-force timetable's scheduled time
  and day type from the archive below, and its route only where the feed
  named none. A day no recorded timetable covers keeps the timetable its
  rows were ingested against: its version name's archived one, or the loaded
  one when the name maps to none. Those that join the static schedule join
  the same timetable's stops. It builds the rest in Postgres; of those, dwell
  and running time take each stop visit's scheduled times from the same
  archive, through the ClickHouse scan that pairs its arrival with its
  departure. A ClickHouse that predates one of those tables needs
  `make ch-bootstrap` before the next `analyze`.
- Every timetable an agency loads is kept in ClickHouse under its content
  hash: its stop times in `static_stop_times_archive` and its schedule as
  ingest stamps it in `static_schedule_archive`. `static_version_map` records
  which content each version name carried, and `static_version_spans` the days
  each name covers. `load_static` records the incoming timetable's span and
  archives the outgoing timetable before it replaces it, and `analyze`
  archives the loaded one when its content or span is missing.
  `scripts/backfill_timetable_archive.py` fills the archive for names loaded
  before it kept them, from the kept zips; with `--headways` it records again,
  from the same zips, the headways of archived timetables not kept at the
  current `agg_route_headway` version, whether kept at an older one or never
  kept. `static_calendar_archive` keeps each timetable's calendar under its
  version name and content (a calendar-only refetch under the same name keeps
  the first calendar), and `--calendars` keeps it, from the same zips, for
  timetables archived before it was. A ClickHouse that predates these
  tables needs `make ch-bootstrap` before the next `load_static` or `analyze`.
  Where the version in force on a date has other content than the one its
  rows were ingested against, `analyze` writes that date's rows filed under
  it to `updates_filed` (`pipeline/refiled.py`), and its own reads and the
  live scans take them from there instead of joining the archive, for as long
  as `updates_filed_dates` shows the date's filing unchanged. A refiled date
  outside analyze's rebuild scope has no valid write until
  `scripts/write_refiled_history.py --apply` writes it, so it is run again
  after the filing rules (`pipeline.refiled.RULES`) change.
  Postgres mirrors the map in `static_version_contents`, each name at its
  latest content, written right after each map row, so the definition stamp
  names each timetable's content without reading ClickHouse.
  `scripts/fill_version_contents.py` copies the mappings written before the
  mirror existed; a Postgres without the mirror needs `make migrate` before
  the next `load_static` or `analyze`.
- Each day's planned trips are saved into `planned_trips_daily` while the
  timetable that plans the day is loaded: every `analyze` run saves each day
  before today the loaded timetable covers that has no saved count yet, and
  `load_static` first saves the outgoing timetable's days before the incoming
  one starts, today and later days included. The day in progress follows the
  latest timetable loaded that day until it is over. The service-delivered
  rate reads a saved day before the loaded timetable, so a reload no longer
  drops past days from it. Nothing reconstructs the days before the first save.
  A day saved before its stop events were saved beside its trips keeps them
  unknown until `scripts/fill_planned_stop_events.py` computes them from the
  archived timetable in force that day, only where that timetable's calendar
  is archived and its planned trips equal the day's saved count.
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
| `make catch-up-from-r2` | Plan catching every agency up from R2, timetable loads interleaved by day (`APPLY=1` runs it, `ANALYZE=1` analyzes after, `ALLOW_GAPS=1` passes a day R2 lacks and loads a timetable after days with no rows, `SEED_DAYS=3` starts an agency with no rows 3 JST days back, `AGENCY="8 53"` narrows it) |
| `make check-aggs` | Detect stale aggregate tables; list days still held from the live copy |
| `make digest` | Generate the daily delay digest (Markdown, ja/en) |
| `make ingest-weather` | Ingest daily weather observations (kill-switched by `WEATHER_INGEST_ENABLED`) |
| `make reprobe-rt-coverage` | Re-probe each agency whose RT field-coverage verdict is missing, refuted or about to lapse; probes only in service hours, so schedule it daily in the late morning |
| `make ask-eval` | Check the Ask gold set's stored signatures still match canonicalization (no database; pytest runs it too) |
| `make prune-query-log` | Delete `ask_query_log` rows older than 90 days (the API also runs this daily) |
| `make prune-pipeline-runs` | Delete `pipeline_runs` rows older than 90 days |
| `make prune-admin-audit` | Delete `admin_audit` rows older than 400 days (production `updates`' horizon; the API also runs this daily) |
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
| `/api/admin/snapshots*`, `GET /api/snapshots/{id}`, `GET /api/snapshots/{id}/today` | Frozen snapshots: admins create, list and delete; anyone with the link reads one and today's figures for its query ([guide](docs/features/snapshots.md)) |
| `/api/admin/ask/*` | Ask query log and funnel |
| `/api/admin/architecture/*` | Serves `docs/features/*.md` to the in-product architecture page |
| `/api/admin/api-keys` | Issue/revoke API keys |
| `/api/me/export` | Download everything the app holds about you (JSON) |
| `DELETE /api/me` | Delete your own account and its personal data (confirm with your email) |

Most data endpoints accept `from`, `to`, `dow`, `time_band`, `service`, and
`routes` filters. API documentation is available from FastAPI at `/docs`
while the server is running and `OPENAPI_DOCS_ENABLED` is on (see
Configuration below).

Example:

```bash
# A signed-out call gets the rules-stage answer. An LLM-grounded answer needs
# the session cookie of a signed-in account that an admin approved for it.
curl -X POST http://localhost:8000/api/1/ask \
  -H 'Content-Type: application/json' \
  -H 'Origin: http://localhost:8000' \
  -H 'Cookie: sid=<your session cookie>' \
  -d '{"question":"系統5の遅延は？"}'
```

## Configuration

Copy `.env.example` and set only what your environment needs. Important groups:

- `DATABASE_URL`, `CLICKHOUSE_*`: database connections.
- `GEMINI_API_KEY`, `OPENAI_API_KEY`, `CHAT_PROVIDERS`: Ask provider ladder. With no
  key for any provider listed in `CHAT_PROVIDERS` the API still starts, LLM-backed
  features are disabled and `/api/config` reports `llm_enabled: false`.
- `CRON_SECRET`: protects the internal live-ingest endpoint.
- `GOOGLE_CLIENT_*`, `GITHUB_CLIENT_*`, `SESSION_SIGNING_KEY`,
  `PUBLIC_BASE_URL`, `FRONTEND_BASE_URL`, `ADMIN_EMAILS`: optional admin
  sign-in. Sign-in is for administrators only: `ADMIN_EMAILS` is the one way
  to make a new admin through SSO, and any other account is refused.
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
- `OBJECT_STORE_*`, `AGENCY_IDS`: scheduled archive ingest.

### Feature kill switches

Each key can be overridden from the admin control room's flags page;
`pipeline/flags.py` owns the resolution and caching rules. "Default" is the
value when neither an override nor the env var is set.

| Key | Env var | Default |
| --- | --- | --- |
| `ask_router_enabled` | `ASK_ROUTER_ENABLED` | on |
| `ask_llm_enabled` | `ASK_LLM_ENABLED` | on |
| `ask_followup_enabled` | `ASK_FOLLOWUP_ENABLED` | off |
| `ask_history_enabled` | `ASK_HISTORY_ENABLED` | on |
| `ask_intent_cache_enabled` | `ASK_INTENT_CACHE_ENABLED` | off |
| `ask_query_log_enabled` | `ASK_QUERY_LOG_ENABLED` | on |
| `weather_ingest_enabled` | `WEATHER_INGEST_ENABLED` | off |
| `openapi_docs_enabled` | `OPENAPI_DOCS_ENABLED` | off |
| `perf_debug_enabled` | `PERF_DEBUG_ENABLED` | off |
| `ml_forecast` | `ML_FORECAST_ENABLED` | off |
| `updates_prune_enabled` | `UPDATES_PRUNE_ENABLED` | off |

`weather_ingest_enabled` gates the weather ingest and also the rain panel on
Why: with it off the panel is not mounted and `/weather_delay` is not requested.

`openapi_docs_enabled` gates `/docs`, `/redoc`, and `/openapi.json`;
`.env.example` turns it on for local dev.

`ml_forecast` lets `GET /api/{agency_id}/forecast/heatmap` and
`/forecast/overview` fill a cell from the ML model's fresh forecasts in
`agg_route_forecast_ml`, marked `source: "ml"`; with it off, every cell is the
historical baseline.

`updates_prune_enabled` lets `gtfs_pipeline.py prune-updates` drop ClickHouse
`updates` months past the horizon. The retention table in
`docs/ops-monitoring-deploy.md` says what it drops and how to turn it on.

Every reader page works signed out, whether or not SSO is configured, and so
does `POST /api/{agency_id}/delays/refresh`, under a per-caller rate limit
(per address, or per account when signed in) and a per-agency cooldown. Admin routes need an admin session; the account
routes (`/api/me/*`, saved conversations) need a signed-in caller. Leaving all
OAuth variables unset runs the app
in anonymous-only mode: nobody can sign in through SSO, only the break-glass
account when it is set. Do not commit `.env`, API keys, OAuth secrets, database
passwords, or private keys.

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

### Standalone

One machine with Docker runs the whole app for your own feeds, with no Oracle
collector, R2, VPS or LLM key:

```bash
git clone https://github.com/TripAndCode/transit-app.git && cd transit-app
cp .env.example .env
docker compose --profile standalone up -d --build
```

This starts Postgres, ClickHouse, the schema step, the app on
<http://localhost:8000> (`STANDALONE_PORT` changes it) and the poller. The
poller reads the agencies table every `POLL_INTERVAL_SEC` (default 60), polls
each enabled agency's feed, keeps every poll in the `transit_archive` volume
and stores it as live data. After each UTC day it packs that day's tarball,
then promotes, archive-ingests and analyzes the closed days, as the Oracle
collector and the daily ingest do.

Add an agency. A standard GTFS-RT TripUpdates feed with a GTFS(-JP) static zip
needs the `static_join` and `direct_url` strategies, which `add_agency` does
not set, so seed it from a one-row CSV with the same columns as
`agencies.csv`, then load its timetable:

```bash
cat > my_agency.csv <<'CSV'
agency_name,feed_url,static_url,ingest_strategy,static_strategy
My Agency,https://example.test/trip_updates.pb,https://example.test/gtfs.zip,static_join,direct_url
CSV
docker compose --profile standalone cp my_agency.csv app:/tmp/my_agency.csv
docker compose --profile standalone exec app python gtfs_pipeline.py seed_agencies /tmp/my_agency.csv
docker compose --profile standalone exec app python gtfs_pipeline.py refresh-static
```

`add_agency --name ... --feed-url ...` also works, for a feed whose strategy is
set afterwards on the admin Agencies page. The next poll picks the agency up,
and Pulse shows today's data from the polls that follow.

Not automated yet: choosing the strategy from the feed itself (probing it),
reloading a timetable the agency republishes (run `refresh-static` again), and
uploading the tarballs anywhere off the machine. Back up the `transit_archive`
volume to keep the raw polls.

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
- No commit carries `[skip ci]`, the squash-merge included, so every push to a
  PR runs CI for the merge gate and every merge runs CI on `main`. See
  `AGENTS.md` for the rule and `transit-app-gotchas` for how it behaves.
- Run the relevant checks before opening a PR, then run `make check` when the
  change affects backend behavior.
