# Transit Delay App

Transit Delay App turns Japanese GTFS and GTFS-Realtime feeds into delay
reports, maps, and a bilingual React dashboard. FastAPI serves the API and the
SPA from one origin. Raw realtime events live in ClickHouse; static schedules,
aggregates, and application data live in Postgres/PostGIS.

## Start Here

### Requirements

- Python 3.11+ (CI and the production image both run 3.12)
- [Poetry](https://python-poetry.org/)
- Docker Desktop
- A Gemini API key for the optional Ask LLM fallback

### Local setup

```bash
cp .env.example .env
# Set GEMINI_API_KEY in .env. Other settings have local defaults.
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
`:8000`, as configured by `PUBLIC_BASE_URL`.

## Load Data

The bootstrap database is empty. Choose one path:

```bash
# Fallback: sample the current agency feeds.
poetry run python gtfs_pipeline.py ingest_live
make analyze

# Primary development path: replay dense archives from the Oracle collector.
make fetch-ingest
```

The archive path pulls GTFS-Realtime and static GTFS archives, then runs ingest,
static loading, and aggregation. The live path is useful for local checks but
does not provide the same historical coverage. Add a one-off agency with:

```bash
poetry run python gtfs_pipeline.py add_agency \
  --name "My Agency" --feed-url "https://example.test/feed.pb"
```

## Data Flow

```text
GTFS-RT feeds
    |
    v
Oracle collector -- dense archives --> Cloudflare R2
                                           |
                                           v
                              Railway ingest job
                              ingest -> analyze
                                |             |
                                v             v
                         ClickHouse raw    Postgres aggregates
                                \             /
                                 v           v
                              FastAPI + React SPA
```

- Oracle is the production collector and R2 is the archive handoff.
- Railway's scheduled ingest job reads R2, writes raw events, and rebuilds
  aggregates.
- `ingest_live` is the lower-fidelity fallback when archives are unavailable.
- The API normally reads precomputed `agg_*` tables; narrow time-band queries
  may read ClickHouse directly.
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
| `make ingest FOLDER=./raw_archives` | Load realtime archives |
| `make load_static STATIC_PATH=./raw_archives_static` | Load static GTFS |
| `make analyze` | Rebuild one agency's aggregates |
| `make analyze-all` | Rebuild aggregates for all agencies |
| `make fetch-ingest` | Fetch Oracle archives and run the full local pipeline |
| `make check-aggs` | Detect stale aggregate tables |
| `make digest` | Generate the daily delay digest (Markdown, ja/en) |
| `make ingest-weather` | Ingest daily weather observations (kill-switched by `WEATHER_INGEST_ENABLED`) |
| `make build-rag-index` | Build the Ask RAG index for all agencies |
| `make ask-eval` | CI gate: verify Ask builder coverage against the gold question set |
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
`serve`. `tools/geosql/bootstrap.sh` prints the connection string to add; it
points at the dev Postgres/PostGIS database, so the same read-only rule as
any other dev-database access applies — see `CLAUDE.md`. `make geosql-down`
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
database on `:5433`, regardless of this Makefile's own `DATABASE_URL` default.
That runner also exports `RUN_CH_INTEGRATION=1`, so the ClickHouse-gated tests
actually run rather than silently skipping. `make check` runs `fmt-check`
(verifies formatting, doesn't rewrite files), `lint`, `typecheck`, then `test`.

Example targeted test:

```bash
DATABASE_URL=postgresql://transit:transit@localhost:5544/transit_test \
  GEMINI_API_KEY=test-key \
  poetry run pytest tests/query/test_tool_queries.py -v
```

That fixed `:5544`/`:8124` pair is shared, and a concurrent run against it can
interfere with another's schema mid-test. When a run might overlap with
another one on the same host, use `scripts/run_full_ci.sh` instead — its
header explains why and describes the isolated Postgres/ClickHouse pair it
uses instead:

```bash
scripts/run_full_ci.sh
```

Frontend checks: see `CLAUDE.md`'s Verification commands section for the
full required list to run before opening a PR.

The React Compiler is enabled. Do not add `useMemo`, `useCallback`, or
`React.memo` as performance fixes. User-visible strings require matching `ja`
and `en` translation keys.

## API

The main endpoints are:

| Endpoint | Purpose |
| --- | --- |
| `GET /health` | Liveness check |
| `GET /api/agencies` | List agencies |
| `GET /api/{agency_id}/delays/live` | Latest realtime delays |
| `GET /api/{agency_id}/reports/{type}` | Report data or CSV |
| `GET /api/{agency_id}/delays/heatmap` | Map delay data |
| `POST /api/{agency_id}/conversations/{cid}/messages` | Deterministic Ask tools |
| `POST /api/{agency_id}/ask` | Natural-language Ask fallback |
| `GET /api/auth/{provider}/login` | Start Google or GitHub OAuth |
| `GET /api/admin/users` | Admin user management |

Most data endpoints accept `from`, `to`, `dow`, `time_band`, `service`, and
`routes` filters. API documentation is available from FastAPI at
`/docs` while the server is running.

Example:

```bash
curl -X POST http://localhost:8000/api/1/ask \
  -H 'Content-Type: application/json' \
  -H 'Origin: http://localhost:8000' \
  -d '{"question":"系統5の遅延は？"}'
```

## Configuration

Copy `.env.example` and set only what your environment needs. Important groups:

- `DATABASE_URL`, `CLICKHOUSE_*`: database connections.
- `GEMINI_API_KEY`, `OPENAI_API_KEY`, `CHAT_PROVIDERS`: Ask provider ladder.
- `ASK_FOLLOWUP_ENABLED`, `COPILOT_INSIGHT_ENABLED`, `WEATHER_INGEST_ENABLED`:
  feature kill switches.
- `OPENAPI_DOCS_ENABLED`: gates `/docs`, `/redoc`, and `/openapi.json`. Off
  unless set, so a deployment publishes no schema by default. `.env.example`
  turns it on for local dev.
- `CRON_SECRET`: protects the internal live-ingest endpoint.
- `GOOGLE_CLIENT_*`, `GITHUB_CLIENT_*`, `SESSION_SIGNING_KEY`,
  `PUBLIC_BASE_URL`, `ADMIN_EMAILS`: optional authentication and admin setup.
- `OBJECT_STORE_*`, `AGENCY_IDS`, `RETENTION_DAYS`: scheduled archive ingest.

Leaving all OAuth variables unset runs the app in anonymous-only mode. Do not
commit `.env`, API keys, OAuth secrets, database passwords, or private keys.

### Secret scanning

`make bootstrap` installs a mandatory gitleaks pre-commit hook (see
`.pre-commit-config.yaml`) that scans every commit's staged content before it's
created; bootstrap fails if the hook can't be installed. Re-run `make hooks` on
its own after a clean checkout, a new machine, or a VPS clone — worktrees of the
same clone share one `.git/hooks` directory, so one run covers all of them.
`make doctor` reports whether the hook is currently installed. `make
verify-secrets` runs the same scanner against the full git history on demand,
and CI's `secrets-scan.yml` runs it again on every push/PR as a backstop for
commits made without the hook installed.

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
deploy/                 systemd units and VPS scripts for the loop and ops-monitoring services
frontend/               React SPA and translations
oracle_cloud/           Oracle VM collector agent (v3): archive fetch, R2 sync, health checks, alerting
scripts/                operational tools and review helpers
tests/                  pytest suites (api, pipeline, query, db, frontend, unit) and fixtures
tools/                  optional local dev tools (GeoSQL/Dekart)
docs/features/          feature-specific behavior guides
.claude/                review and autonomous-loop workflows
```

Useful entry points:

- `gtfs_pipeline.py`: command-line pipeline entry point.
- `pipeline/ingest.py`: GTFS-Realtime parsing.
- `pipeline/static_loader.py`: static GTFS loading.
- `pipeline/analyze.py`: aggregate rebuilding.
- `api/main.py`: FastAPI application.
- `frontend/src/`: React application.

## Safety Rules

- Dev databases are read-only; see `CLAUDE.md`. Use the throwaway `:5544`/
  `:8124` pair described above for writes.
- Never push directly to `main`; use reviewed squash-merged PRs.
- Commit messages carry `[skip ci]`, except the last push before a PR is
  readied: its tip must omit the trailer so CI runs and can be green, which
  the merge gate requires. See `CLAUDE.md` for the rule and
  `transit-app-gotchas` for how the trailer behaves.
- Run the relevant checks before opening a PR, then run `make check` when the
  change affects backend behavior.
