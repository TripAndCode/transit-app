# Transit Delay App

Real-time bus delay analysis for Japanese transit agencies. Ingests GTFS-RT protobuf feeds, aggregates delay statistics, and exposes them through a FastAPI REST API plus a React SPA (map heatmap, hourly heatmap, daily trend chart, route polyline overlay, CSV export). Natural-language Japanese questions go through Groq (llama-3.3-70b-versatile) tool-use against six deterministic SQL tools.

---

## Requirements

- Python 3.11+
- [Poetry](https://python-poetry.org/)
- [Docker Desktop](https://www.docker.com/products/docker-desktop/)
- A [Groq API key](https://console.groq.com/) (free tier is sufficient)

---

## Quick Start

### TL;DR — Path B (Oracle Cloud archives, current setup)

```bash
# one-time
cp .env.example .env && $EDITOR .env   # set GROQ_API_KEY + Oracle vars
make install                            # python deps
make frontend-install                   # npm deps

# every run
make db                                 # start Postgres on :5433
make seed-agencies                      # upsert agencies.csv (Aomori included)
make fetch-ingest                       # rsync from Oracle VM + ingest + analyze
make serve                              # terminal A — FastAPI on :8000
make frontend-dev                       # terminal B — Vite on :5173
```

Open <http://localhost:5173>. The Ask tab works once `GROQ_API_KEY` is valid;
the Map / Live / Reports tabs render real content after the fetch+ingest step.

For live HTTP fetch from each agency's GTFS-RT URL instead of the Oracle
archives, see [Path A](#path-a--direct-live-fetch-uses-agenciesfeed_url).

### Detailed steps

#### 1. Start the database

```bash
make db
```

Builds a PostGIS 14 + pgvector image, starts a `transit-pg` container on
port 5433, and applies the schema. Everything lives inside this project —
no external setup needed.

#### 2. Install dependencies

```bash
make install            # python (poetry)
make frontend-install   # npm (frontend/)
```

#### 3. Set environment

Create `.env` in the project root (gitignored, loaded automatically by `make`):

```
DATABASE_URL=postgresql://transit:transit@localhost:5433/transit
GROQ_API_KEY=your_groq_api_key_here
# Path B (Oracle Cloud archives) only:
ORACLE_HOST=64.110.114.101
ORACLE_USER=opc
ORACLE_SSH_KEY_PATH=/Users/you/transit-app/oracle_cloud/ssh-key-2026-03-28.key
AGENCY_ID=1
```

See `.env.example` for the full list.

#### 4. Register agencies

Edit `agencies.csv` (committed) and run:

```bash
make seed-agencies      # idempotent upsert on feed_url
```

The CSV has columns `agency_name, feed_url, static_url, trip_id_pattern`.
For one-off ad-hoc inserts you can still use the CLI:

```bash
poetry run python gtfs_pipeline.py add_agency \
  --name "My Agency" --feed-url "https://..."
```

If your agency uses a non-standard `trip_id` format, set the
`trip_id_pattern` column to a named-group regex (e.g.
`^(?P<service>.+?)_(?P<hour>\d+)h(?P<minute>\d+)_route(?P<route>\d+)$`).

#### 5. Load data

**Path B — pull archives from Oracle Cloud (rsync + ingest + analyze):**

```bash
make fetch-ingest
```

> Run via `make` — the Makefile's `-include .env` + `export` makes the
> `ORACLE_HOST` / `ORACLE_USER` / `ORACLE_SSH_KEY_PATH` vars visible to
> the script. Calling `bash scripts/fetch_and_ingest.sh` directly will
> fail because raw shells don't auto-source `.env`.

**Path A — live fetch each agency's GTFS-RT URL:**

```bash
poetry run python gtfs_pipeline.py ingest_live
```

Path A skips static GTFS — for stop coordinates / heatmap, also run
`make load_static PATH=./raw_archives_static` against a downloaded zip.

Both paths end at `make analyze` to refresh aggregates (Path B's script
does this for you).

#### 6. Start the backend

```bash
make serve          # :8000 (override with PORT=9000)
```

Interactive docs: <http://localhost:8000/docs>.

#### 7. Start the frontend (in a second terminal)

```bash
make frontend-dev   # Vite on :5173
```

Open <http://localhost:5173>. With no data, all tabs show empty states.

> No agencies yet? Append `?admin=1` to the URL to expose the in-browser
> "+ 新規事業者" form, or edit `agencies.csv` and re-run `make seed-agencies`.

---

## Data ingest: Path A vs Path B

Two ways to get GTFS data into the local Postgres. Both end at the same
`updates` / `static_*` / `agg_*` tables and the API/UI don't care which one
fed them. Pick one based on what the agency exposes.

### Path A — Direct live fetch (uses `agencies.feed_url`)

`ingest_live` HTTP-GETs the agency's official GTFS-RT endpoint, parses the
protobuf, and writes one row per `(trip_id, captured_at)` into `updates`.
No external server involved.

```
agency.feed_url ──HTTP GET──▶ parse .pb ──▶ updates table
```

```bash
poetry run python gtfs_pipeline.py ingest_live
```

Needs: a public GTFS-RT URL, internet, a populated `feed_url` on the agency
row. **This is what production uses** — a GitHub Actions workflow hits
`POST /internal/cron/ingest` hourly, and the FastAPI app runs `ingest_live`
+ `analyze` for every agency in a background task. See
[Deployment](#deployment-linode-vps-tokyo) below.

Path A does **not** load static GTFS (stops, routes, timetable) — for that
you still need `make load_static` against a local zip, or set `static_url`
on the agency and add a fetcher (not in this repo).

### Path B — Oracle Cloud archive replay (local dev only)

A separate Oracle Cloud VM (`64.110.114.101`, user `opc`) runs an
independent scraper that crawls the GTFS-JP website and stores both:

- `archive/*.tar.gz` — historical GTFS-RT protobuf bundles
- `static_archive/gtfs_static_*.zip` — static GTFS bundles (stops/routes/timetable)

The local box never crawls. It **pulls** those archives over SSH, then runs
ingest + load_static + analyze locally:

```
Oracle VM (crawls GTFS-JP)
  ├─ /home/opc/.../archive/*.tar.gz
  └─ /home/opc/.../static_archive/gtfs_static_*.zip
       │
       │   scripts/fetch_archives.sh   (rsync over SSH)
       ▼
   raw_archives/ + raw_archives_static/   (local)
       │
       │   ingest + load_static + analyze
       ▼
   Postgres (transit-pg, port 5433)
```

The wiring is in `scripts/fetch_archives.sh` (rsync) and
`scripts/fetch_and_ingest.sh` (rsync → ingest → load_static → analyze).
This is **only useful for local development** — production deploys do
not have SSH access to the Oracle VM, and the cron path runs `ingest_live`
against each agency's `feed_url` instead.

For the full bring-up command sequence see [Quick Start ▸ TL;DR](#tldr--path-b-oracle-cloud-archives-current-setup) above. `feed_url` on the agency row is metadata only for Path B — the .pb files are pre-fetched.

> First `fetch_archives.sh` run can take a while (full rsync of every
> archive in the Oracle VM). Subsequent runs only pull deltas.

---

## Database

| Command | Effect |
|---|---|
| `make db` | Build image, start container, apply schema |
| `make db-down` | Stop container (data volume preserved) |
| `make migrate` | Apply pending migrations (`db/migrations/*.up.sql`) |
| `make migrate-down` | Roll back the latest migration |
| `docker compose down -v` | Stop and delete data volume |

Data is stored in a named Docker volume (`transit-app_transit_pgdata`) — it survives container restarts.

### Migrations

Each schema change ships as a numbered up/down pair under `db/migrations/`. Run `make migrate` (dev) or `docker compose exec app python gtfs_pipeline.py migrate up` (prod) after pulling new migrations; `db.migrate` records applied versions in `schema_migrations`.

| File | Adds |
|---|---|
| `0001_initial` | core tables + PostGIS / pgvector extensions |
| `0002_trip_id_pattern` | `agencies.trip_id_pattern` for per-agency parsing |
| `0003_api_keys` | API-key registry for the optional Pro tier |
| `0004_stop_codes` | `static_stops.stop_code` + `platform_code` (surfaced in map tooltip) |
| `0005_static_shapes` | `static_shapes` table for route polyline geometry |
| `0006_strategy_columns` | `agencies.ingest_strategy` + `static_strategy` |
| `0007_static_trips_service_id` | `static_trips.service_id` for calendar joins |
| `0008_static_routes_long_name` | `static_routes.route_long_name` for richer filters |
| `0009_auth` | `users`, `oauth_identities`, `sessions`, `login_events`, `filter_presets` |
| `0010_audit_kinds` | widens `login_events.kind` to include `account_created` + `login_failed` |

---

## Pipeline

All commands go through `gtfs_pipeline.py`. `make` targets forward to it.

### Ingest GTFS-RT archives

```bash
make ingest FOLDER=./raw_archives
make ingest FOLDER=./raw_archives AGENCY_ID=1   # with explicit agency
```

Reads `.pb` files from tarballs and loose files. Deduplication key is `{date_dir}/{pb_name}`.

### Live ingest (scheduled)

```bash
poetry run python gtfs_pipeline.py ingest_live
poetry run python gtfs_pipeline.py ingest_live --agency-id 1
```

Fetches the current GTFS-RT protobuf from each agency's `feed_url`. In production it is invoked hourly by a GitHub Actions workflow that hits the guarded `POST /internal/cron/ingest` endpoint — see [Deployment](#deployment-linode-vps-tokyo).

### Load static GTFS

```bash
make load_static PATH=./raw_archives_static
```

Loads `stops.txt`, `stop_times.txt`, `trips.txt`, `routes.txt`, `calendar_dates.txt` from the latest `*_static.zip`. Required for stop-name resolution and heatmap.

### Run aggregation

```bash
make analyze
```

Computes five aggregation tables used by all API queries:

| Table | Key |
|---|---|
| `agg_route_stats` | per route × service type |
| `agg_route_hour` | per route × service type × hour |
| `agg_route_dow` | per route × service type × day of week |
| `agg_daily_trend` | per route × service type × date |
| `agg_stop_seq` | per route × stop sequence |

---

## API

### Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Liveness check |
| `GET` | `/api/agencies` | List agencies |
| `POST` | `/api/agencies` | Register agency |
| `GET` | `/api/agencies/{id}` | Get agency |
| `POST` | `/api/{agency_id}/ask` | Natural-language question → Japanese answer (Groq tool-use) |
| `POST` | `/api/{agency_id}/query` | Structured intent dict → rows + Japanese answer |
| `GET` | `/api/{agency_id}/reports` | List pre-computed reports |
| `GET` | `/api/{agency_id}/reports/{type}` | Report payload (`format=json` default, `csv` for download) |
| `GET` | `/api/{agency_id}/delays/live` | Latest delay per trip |
| `GET` | `/api/{agency_id}/delays/heatmap` | GeoJSON delay heatmap by stop (range/DOW/time-band/route filtered) |
| `GET` | `/api/{agency_id}/route-shape` | Stop sequence + per-stop avg delay for one route (powers the map polyline) |
| `GET` | `/api/{agency_id}/today/route-summary` | Per-route operational summary for the most recent observation date |
| `GET` | `/api/{agency_id}/routes` | Static route list (with derived `route_code`) |
| `GET` | `/api/{agency_id}/stops` | Static stop list |
| `GET` | `/api/auth/{provider}/login` | Start OAuth (provider = `google` \| `github`); 302 to provider |
| `GET` | `/api/auth/{provider}/callback` | OAuth callback; mints `sid` cookie, redirects to sanitized `next` |
| `POST` | `/api/auth/logout` | Delete session row + clear `sid` cookie (Origin-checked) |
| `GET` | `/api/me` | Caller profile + linked OAuth identities |
| `GET` | `/api/me/sessions` | Caller's active sessions (sid truncated) |
| `DELETE` | `/api/me/sessions/{prefix}` | Revoke a specific session by 12-char sid prefix |
| `GET` | `/api/me/presets?agency_id=` | Caller's saved filter presets |
| `POST` | `/api/me/presets` | Save current filter as a named preset |
| `DELETE` | `/api/me/presets/{id}` | Delete a saved preset |
| `GET` | `/api/admin/users` | Admin: list users (`q`, `role`, `suspended`, `limit`, `offset`) |
| `GET` | `/api/admin/users/{uid}` | Admin: user detail + identities + last 20 audit events |
| `PATCH` | `/api/admin/users/{uid}` | Admin: change `role` / `suspended` (self-guard + last-admin guard) |
| `DELETE` | `/api/admin/users/{uid}` | Admin: soft-delete (anonymize PII, drop sessions + identities) |

The data endpoints under `/api/{agency_id}/*` accept the global filter
context as query params: `from`, `to`, `dow`, `time_band`, `service`,
`routes` (comma-joined). The frontend persists these in the URL via
`useRangeContext`.

### Rate limits

| Tier | Limit | How |
|---|---|---|
| Free (no key) | 60 req/min per IP | — |
| Pro | 600 req/min | `X-API-Key: <key>` header |

### Example

```bash
curl -X POST http://localhost:8000/api/1/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "系統5の遅延は？"}'
```

Query types understood: `ranking`, `by_hour`, `by_dow`, `by_stop`, `by_date`, `trend`, `on_time`, `compare`, `worst_5min`, `stop_ranking`, `dow_ranking`, `compare_ranking`, `stop_list`, `routes_at_stop`, `route_info`, `timetable`.

## Authentication & user management

Anonymous browsing is unchanged. Logging in (Google or GitHub) unlocks
saved filter presets; admins (set via `ADMIN_EMAILS`) can manage users
at `/admin/users`.

### OAuth setup

Google: create an OAuth 2.0 Client at console.cloud.google.com (type =
Web). Authorized redirect URIs:
- `http://localhost:8000/api/auth/google/callback` (dev)
- `https://<DOMAIN>/api/auth/google/callback` (prod)

GitHub: github.com/settings/developers → New OAuth App. Callback URLs:
- `http://localhost:8000/api/auth/github/callback` (dev)
- `https://<DOMAIN>/api/auth/github/callback` (prod)

Set `GOOGLE_CLIENT_ID/SECRET`, `GITHUB_CLIENT_ID/SECRET`,
`SESSION_SIGNING_KEY` (`openssl rand -hex 32`), and
`PUBLIC_BASE_URL` in `.env`.

### First admin

Add your email to `ADMIN_EMAILS` (comma-separated). On your first
login, that user row is promoted to `role='admin'` and an audit
event is recorded. Subsequent admins are promoted via the console
at `/admin/users`. Removing an email from `ADMIN_EMAILS` does not
auto-demote — use the console.

### Flow

```
                                           ┌──────────────┐
   Browser  ──── GET /api/auth/{p}/login ─►│  FastAPI     │
                                           │              │  mint state + PKCE
                                           │              │  sign oauth_tx cookie
   Browser  ◄─── 302 to provider auth ─────│              │
                ✓ sets oauth_tx (5 min, signed, httponly)
        │
        ▼
   Provider consent screen (Google / GitHub)
        │
        ▼
   Browser  ──── /api/auth/{p}/callback?code&state ───►  FastAPI
                                                            │
                                                            ▼
                                          ┌─── verify signature, state, provider ───┐
                                          │ FAIL                                    │ OK
                                          ▼                                         ▼
                              insert login_failed(reason)              exchange code w/ PKCE
                              302 → /login?error=...                          │
                                                                              ▼
                                                                      fetch userinfo
                                                                              │
                                                            ┌─ email missing / unverified ─┐
                                                            ▼                              ▼
                                              insert login_failed(reason)            BEGIN txn
                                              302 → /login?error=...                       │
                                                                                   match by (provider, sub)
                                                                                   else upsert by email
                                                                                   ├─ new user? → account_created
                                                                                   ├─ ADMIN_EMAILS? → role_changed
                                                                                   ├─ insert session
                                                                                   └─ insert login
                                                                                          │
                                                                                  COMMIT; set sid cookie
                                                                                  302 → <next>

   Subsequent requests:  Browser sends sid cookie → middleware does SELECT session JOIN users
                         touches last_seen_at at most 1/min/sid

   Logout:               POST /api/auth/logout (Origin checked)
                         → delete session, insert logout
                         → 204 + clear sid cookie
```

**Audit kinds emitted** (table `login_events`):
- `account_created` — first time the user row is INSERTed
- `login` — successful session created
- `login_failed` — callback aborted (`state`, `provider_down`, `unverified_email`, `no_email`); `user_id` is null
- `logout` — user-initiated session deletion
- `role_changed` — admin promotion / demotion (via `ADMIN_EMAILS` bootstrap or console)
- `suspended` / `unsuspended` — admin console toggles
- `deleted` — admin console soft-delete (PII anonymized, sessions revoked)

Each row carries `ip`, `user_agent`, `provider`, optional `meta` JSONB, and `actor_id` (who performed the action — same as `user_id` for self-actions, admin's uid for admin-actions).

---

## Frontend

Single-page React app at `frontend/` (Vite + TypeScript strict + TanStack Query + react-router-dom + MapLibre GL). Default tab is the map; tabs cover ask (NL questions), live delays (most-recent-observation cards), and pre-rendered reports (with hourly heatmap and daily-trend chart). UI chrome is Japanese.

Key v2 components (all under `frontend/src/components/`):

- `RangeBadge` + `TabFilterBar` — unified date-range / DOW / time-band / service / route filter strip; state lives in URL params and persists across tab switches.
- `MapLegend` — draggable, position-persisted overlay explaining the delay-severity color ramp.
- `charts/DailyChart` — sample-weighted line chart for trend reports.
- `charts/HourlyHeatmap` — date × hour-of-day heatmap; click a row label / column / cell to drill the global filter into that time-band / day / both.
- `ReportTable` — inline horizontal bars colored by severity for ranking/compare reports; CSV export with Japanese headers.
- Map tooltips show stop name, GTFS `platform_code` (のりば badge), `stop_code`, contributing route_codes, and the active filter period.

### Local dev

```bash
make frontend-install     # one-time: install npm deps
make serve                # in one shell — FastAPI on :8000
make frontend-dev         # in another  — Vite dev server on :5173
```

The dev server proxies `/api` and `/health` to FastAPI; everything else is owned by the SPA, so direct reloads on `/agencies/:id/map` etc. work in dev. Set `CORS_ORIGINS=http://localhost:5173` in `.env`.

Open http://localhost:5173. Append `?admin=1` to any URL to expose the agency-creation form.

### Build (also runs in CI / Docker)

```bash
make frontend-build       # outputs to frontend/dist
```

### Production

The Dockerfile uses a multistage build that compiles `frontend/` and copies `dist/` into `api/static/`. FastAPI mounts it at `/`, so a single container serves both the API and the SPA — no CORS, one URL.

### Frontend env vars

| Var | Purpose | Default |
|---|---|---|
| `VITE_API_BASE_URL` | Override API origin (split-deploy only) | `""` (same-origin) |
| `VITE_MAP_STYLE_URL` | Override map style URL | (in-code OSM raster) |

---

## Architecture

```
GTFS-RT .pb files / live feed_url
    │
    ▼
pipeline/ingest.py          zero-dependency protobuf parser → updates table
pipeline/static_loader.py   GTFS Static zip → static_* tables
pipeline/analyze.py         SQL aggregations → agg_* tables
    │
    ▼
api/main.py                 FastAPI app (asyncpg pool, Asia/Tokyo session, SPA static fallback)
api/middleware/
  auth.py                   X-API-Key validation → request.state.tier (free / pro)
  ratelimit.py              slowapi 60/min free, 600/min pro
  session.py                sid cookie → DB lookup → request.state.user (1/min last_seen throttle)
api/routers/
  agencies.py               agency CRUD
  ask.py                    NL question → Groq tool-use → answer (pipeline/query/chat)
  query.py                  structured intent dict → execute → format
  reports.py                pre-computed report payloads (json + csv)
  map.py                    live delays + heatmap + route-shape + today summary
  static.py                 route/stop lists
  internal.py               POST /internal/cron/ingest (CRON_SECRET-gated)
  auth.py                   OAuth login / callback / logout (Authlib + signed oauth_tx cookie)
  me.py                     self-service /api/me, sessions, filter presets
  admin.py                  /api/admin/users CRUD with self + last-admin guards
api/oauth.py                Authlib OAuth client registry (Google OIDC, GitHub)
api/security.py             User dataclass + require_user / require_admin / csrf_guard deps
    │
    ▼
api/range.py                shared RangeCtx (from/to/dow/time_band/service/routes) + SQL filter builder
pipeline/query/
  chat.py                   Groq tool-use chat (six tools, system prompt, date overrides)
  tools.py                  the six tool implementations + route validation
  executor.py               legacy SQL executors used by /query and tools
  formatter.py              Python templates → Japanese text
pipeline/audit.py           one-row INSERT into login_events (caller owns the txn)
pipeline/reports.py         compute_* aggregations (cached via async_lru_cache)
pipeline/cache.py           bounded async LRU + TTL decorator
```

**Trip ID format (default):** `{service_type}_{HH}時{MM}分_系統{route_code}`  
Custom formats are configurable per agency via `trip_id_pattern` in the `agencies` table.

---

## Development

```bash
make fmt       # ruff format
make lint      # ruff check
make test      # pytest (requires DATABASE_URL + running container)
make check     # fmt + lint + test
```

Run a specific test file:

```bash
DATABASE_URL=postgresql://transit:transit@localhost:5433/transit \
  GROQ_API_KEY=test-key \
  poetry run pytest tests/test_executor.py -v
```

---

## Deployment (Linode VPS, Tokyo)

Single 2 GB Linode runs `docker compose --profile prod` — FastAPI + bundled SPA, Caddy (auto-HTTPS), and PostGIS+pgvector together on one box. ~$12/mo. Cron is a GitHub Actions workflow that hits a guarded `POST /internal/cron/ingest` hourly — no always-on cron worker.

Full step-by-step (provision → harden → docker → DNS → backups): [`docs/deploy-linode.md`](docs/deploy-linode.md).

Domain ideas (portfolio): `transit-delay.app`, `gtfs-jp.dev`, `chien-map.app` (遅延 = delay), `jptransit.app`. Cheapest registrars for `.app`/`.dev` are Cloudflare Registrar (~$12–14/yr, no markup, free WHOIS privacy).

### Required env (read by `compose.yml` prod profile)

| Variable | Notes |
|---|---|
| `GROQ_API_KEY` | from console.groq.com |
| `CRON_SECRET` | `openssl rand -hex 32`; must match the GH Actions repo secret of the same name |
| `POSTGRES_PASSWORD` | `openssl rand -hex 24`; used by db + injected into app's `DATABASE_URL` |
| `CADDY_SITE_ADDRESS` | `:80` for IP-only first boot; your domain (e.g. `transit-delay.app`) once DNS is wired |
| `CORS_ORIGINS` | leave empty — SPA + API are same-origin behind Caddy |

### Required GitHub repo secrets

| Name | Notes |
|---|---|
| `CRON_SECRET` | random 32 bytes; same value as `CRON_SECRET` in the server's `.env` |
| `APP_BASE_URL` | e.g. `https://transit-delay.app` (the GH workflow `curl`s `${APP_BASE_URL}/internal/cron/ingest`) |

### Operational notes

- Migrations: not auto-run. After `git pull && docker compose --profile prod up -d --build`, run `docker compose exec app python gtfs_pipeline.py migrate up` if the pull included new migrations.
- The hourly cron is observable in the GitHub Actions tab. Manual replay: `gh workflow run "Hourly Ingest"`.
- `make fetch-ingest` (Oracle SSH replay) is **local-dev only** and not part of any deployed cron path. The deployed cron uses `ingest_live` — direct HTTPS GET of each agency's `feed_url`.
