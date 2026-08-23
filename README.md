# Transit Delay App

Real-time bus delay analysis for Japanese transit agencies. Ingests GTFS-RT protobuf feeds, aggregates delay statistics, and exposes them through a FastAPI REST API plus a React SPA (map heatmap, hourly heatmap, daily trend chart, route polyline overlay, CSV export, threaded Q&A with persistent conversations, and a 最新観測 triage tab that ranks routes by deviation from their historical baseline with per-trip / per-stop drilldown). The Ask tab is a chat-first interface: a bottom-pinned dock of five parameterized question chips (`top_n` / `on_time` / `trend` / `cmp_service` / `route_stats`) dispatches deterministic SQL tools — no LLM on the primary path. Optional LLM-grounded follow-up chips interpret the result that's already on screen, gated behind `ASK_FOLLOWUP_ENABLED` per the kill-switch policy.

---

## Requirements

- Python 3.11+
- [Poetry](https://python-poetry.org/)
- [Docker Desktop](https://www.docker.com/products/docker-desktop/)
- A [Groq API key](https://console.groq.com/) (free tier is sufficient)

---

## Quick Start

### One-shot bootstrap

From a fresh checkout — installs deps, starts the DB, applies migrations,
seeds agencies, builds and bakes the SPA into `api/static/` for
single-origin serve:

```bash
cp .env.example .env && $EDITOR .env   # at minimum, set GROQ_API_KEY
make bootstrap                          # ≈ 2–4 min on a clean machine
make doctor                             # sanity check — env, db, port, baked SPA
make serve                              # FastAPI on :8000 (serves SPA + API)
```

Open <http://localhost:8000>.

`make bootstrap` is idempotent — re-run any time the project state drifts
(deps changed, migrations added, frontend rebuilt). `make doctor` is
informational only; it never starts or stops anything.

### What you get out of the box

| Tab     | Works after bootstrap?            | Needs                                                    |
|---------|-----------------------------------|----------------------------------------------------------|
| Ask     | once `GROQ_API_KEY` is valid       | a real Groq key in `.env`                                |
| Map / Live / Reports | empty until data lands             | one of the [data-load paths](#load-data) below           |
| Login   | hidden in anonymous-only mode      | the [SSO env block](#authentication--user-management)    |
| Admin   | `/admin/users` once you log in      | your email in `ADMIN_EMAILS`                             |

### Two dev topologies

`make serve` alone gives you **single-origin** dev: FastAPI on `:8000` serves
both the API and the SPA out of `api/static/`. SSO cookies, CSRF, and
proxying all behave the way they will in production. Recommended.

For a **hot-reload SPA loop** (Vite on `:5173`, FastAPI on `:8000`), run
`make frontend-dev` in a second terminal and open
<http://localhost:5173>. Vite proxies `/api` and `/health` to `:8000`, and
both CORS + CSRF defaults already allow `http://localhost:5173`, so no
env edits are needed. Note: **OAuth callbacks land on :8000 directly**
(the provider redirects to `PUBLIC_BASE_URL`). After login the browser
is on `:8000`; switch the tab back to `:5173` and the SPA sees the
session because `localhost:5173` and `localhost:8000` are same-site (eTLD+1 = `localhost`)
and the API sends `Access-Control-Allow-Credentials: true`. SSO
end-to-end works on either origin; you just authenticate via `:8000`
once per session.

## Ask tab — how it works

The Ask tab is **chat-first and deterministic**. A bottom dock of five
parameterized chips — 🏆 Top-N delays, 🎯 On-time rate, 📈 Route delay trend,
⚖️ Weekday vs Weekend, 🚏 Route overview — dispatches SQL tools directly
(`POST /conversations/{cid}/messages` → `pipeline.query.tools.dispatch`).
**No LLM on this path.** Each answer bubble gets follow-up chips (why /
reliability / slice / summarize / next) that call the LLM *only* to interpret
the result already on screen.

Free-text questions (`POST /api/{agency_id}/ask`, plus the anonymous
build-sentinel path) go through a 3-stage router so most never reach the LLM:

1. **Rules** — regex/keyword → tool args (`pipeline/query/router.py`).
2. **Embedding** — `intfloat/multilingual-e5-small` nearest-neighbor against
   `rag_chunks`; dispatch directly above a cosine-similarity cutoff.
3. **RAG + LLM** — long tail only: top-3 golden examples are few-shot-injected,
   then a provider (Cerebras → Groq → Ollama) picks the tool.

Stages 1–2 use no LLM, so rankings/lists/counts keep working even when every
provider's quota is spent. Rebuild the stage-2 index after editing
`tests/ask_eval/golden_set.jsonl`:

```bash
poetry run python gtfs_pipeline.py build_rag_index --agency-id 1   # or: make build-rag-index
```

The stage internals, provider ladder, intent cache, and the anonymized
`ask_query_log` live in `pipeline/query/` — see the [module map](#module-map).

| Flag | Default | Effect |
|---|---|---|
| `ASK_FOLLOWUP_ENABLED` | `false`¹ | Off → follow-up chips hidden, `POST /followup` returns 503. The LLM kill-switch. |
| `ASK_INTENT_CACHE_ENABLED` | `false` | Enables the canonical-intent cache + guided builder UX. |
| `ASK_HISTORY_ENABLED` | `true` | Off → the LLM stage gets no conversation memory. |
| `ASK_QUERY_LOG_ENABLED` | `true` | Off → no rows written to `ask_query_log`. |
| `ASK_ROUTER_ENABLED` | `true` | Off → skip stages 1–2 (every question goes to the LLM). |

¹ Code-level fallback when unset. `.env.example` ships it `true` as the
local-dev default (so a fresh `make bootstrap` dogfoods it with no manual
flag-flipping) — a real deploy sets its own env vars and is unaffected.

## Load data

`make bootstrap` doesn't pull any GTFS-RT data — the DB is empty until
you choose a load path:

```bash
# Path A — live fetch from each agency's official feed_url (fallback)
poetry run python gtfs_pipeline.py ingest_live
make analyze

# Path B — replay archives from the Oracle Cloud collection VM
make fetch-ingest    # rsync + ingest + load_static + analyze in one shot
```

Path B (the dense Oracle archive) is the real data path — locally it pulls
over SSH; in production a daily Railway job pulls the same archives from object
storage. Path A (`ingest_live`) is the lower-fidelity no-Oracle fallback. See
[Path A vs Path B](#data-ingest-two-paths) for the why.

See [Deployment](#deployment-railway) for how the production ingest job is wired.

For one-off ad-hoc agency inserts:

```bash
poetry run python gtfs_pipeline.py add_agency \
  --name "My Agency" --feed-url "https://..."
```

If the agency uses a non-standard `trip_id` shape, set
`trip_id_pattern` on the row to a named-group regex like
`^(?P<service>.+?)_(?P<hour>\d+)h(?P<minute>\d+)_route(?P<route>\d+)$`.

> **SSO is optional.** Leaving all five auth env vars unset
> (`SESSION_SIGNING_KEY`, `GOOGLE_CLIENT_ID/SECRET`,
> `GITHUB_CLIENT_ID/SECRET`) boots in anonymous-only mode — the login
> link is hidden and `/api/auth/*` is unmounted. To wire up SSO + the
> `/admin/users` console see
> [Authentication & user management](#authentication--user-management).
> A *partial* set is rejected at startup since a half-wired OAuth flow
> would leak state cookies without ever completing.

## Reset / re-bootstrap

```bash
make db-down                           # stop container, KEEP volume
docker compose down -v                  # stop and DELETE the data volume
make bootstrap                          # bring everything back up
```

## Quickstart cheat sheet

```bash
make bootstrap        # first-run setup (install + db + migrate + seed + frontend bake)
make doctor           # sanity check env / db / port / baked SPA
make serve            # FastAPI on :8000 (single-origin: SPA + API together)
make frontend-dev     # optional second terminal — Vite hot reload on :5173
make fetch-ingest     # Path B — pull archives from Oracle VM and ingest
make analyze          # recompute the agg_* tables (after a fresh ingest)
make db-down          # stop Postgres, keep the volume
```

---

## Data ingest: two paths

Both paths land in the same `updates` (ClickHouse) / `static_*` / `agg_*`
(Postgres) tables; the API doesn't care which fed them. See the
[architecture diagram](#runtime--data-flow) for how they fit together.

- **Path B — Oracle archive (primary).** A collector VM archives GTFS-RT and
  static GTFS. Locally, `make fetch-ingest` rsyncs the archives over SSH and
  runs ingest → load_static → analyze (`scripts/fetch_and_ingest.sh`). In
  production the same archives arrive via object storage and a daily Railway
  job — see [Deployment](#deployment-railway). Dense (~30s) observations make
  it the higher-fidelity source.
- **Path A — live fetch (fallback).** `ingest_live` HTTP-GETs each agency's
  `feed_url` and parses the protobuf. No archive server needed, but it samples
  the live feed rather than the full window, and does **not** load static GTFS
  (use `make load_static` for stop names / shapes).

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

`make db` also brings up ClickHouse (raw `updates`, ~575M rows across 4
agencies) and applies `db/clickhouse/schema.sql` via `make ch-bootstrap` —
see [`db/clickhouse/`](db/clickhouse/). `make ch-test` starts the separate
throwaway ClickHouse instance tests run against (`:8124`); set
`RUN_CH_INTEGRATION=1` to include ClickHouse-gated tests in a `pytest` run.

### Migrations

Each schema change ships as a numbered up/down pair under `db/migrations/`. Run `make migrate` (dev) or `docker compose exec app python gtfs_pipeline.py migrate up` (prod) after pulling new migrations; `db.migrate` records applied versions in `schema_migrations`.

Migrations are self-describing by filename; browse `db/migrations/` for the full list (currently through `0024_agg_route_hour_dow`).

### Optional: GeoSQL / Dekart (local spatial analysis)

[GeoSQL](https://github.com/dekart-xyz/geosql) is a third-party Claude Code
skill for ad hoc geospatial SQL with a map-in-the-loop agent flow. `make
geosql-up` brings up a local, self-hosted
[Dekart](https://github.com/dekart-xyz/dekart) container
(`tools/geosql/compose.yml`) so `/geosql` prompts can query and render maps
from this repo's dev Postgres/PostGIS data (`static_stops.geom`,
`static_shapes.geom`). Fully local — no data leaves the machine, no Dekart
Cloud. (ClickHouse isn't wired in: it has no spatial columns to render, and
Dekart's connection-management mode doesn't support ClickHouse queries.)

Setup: `pipx install geosql && geosql` (installs the `/geosql` skill), then
`make geosql-up` followed by `tools/geosql/bootstrap.sh` (waits for Dekart to
be ready and prints the connection string to paste into the Dekart UI at
`localhost:8080`).

**This connection points at the same real, read-only dev Postgres instance
documented above — never run write/DDL queries through GeoSQL/Dekart.**
`make geosql-down` tears the stack down.

---

## Pipeline

All commands go through `gtfs_pipeline.py`. `make` targets forward to it.

### Ingest GTFS-RT archives

```bash
make ingest FOLDER=./raw_archives
make ingest FOLDER=./raw_archives AGENCY_ID=1   # with explicit agency
```

Reads `.pb` files from tarballs and loose files, writes rows to ClickHouse's
`updates` table (batched, not per-file — see `pipeline/clickhouse.py`).
Deduplication key is `{date_dir}/{pb_name}`, tracked via ClickHouse's own
distinct `file_name` values (no separate file-tracking table).

### Live ingest (scheduled)

```bash
poetry run python gtfs_pipeline.py ingest_live
poetry run python gtfs_pipeline.py ingest_live --agency-id 1
```

Fetches the current GTFS-RT protobuf from each agency's `feed_url`. This is the production *fallback* path — invoked via the guarded `POST /internal/cron/ingest` endpoint when object storage isn't wired; the primary path is the daily Oracle-archive ingest job (see [Deployment](#deployment-railway)).

### Load static GTFS

```bash
make load_static STATIC_PATH=./raw_archives_static
```

Loads `stops.txt`, `stop_times.txt`, `trips.txt`, `routes.txt`, `calendar_dates.txt` from the latest `*_static.zip`. Required for stop-name resolution and heatmap.

### Run aggregation

```bash
make analyze
```

Reads and dedups ClickHouse's `updates`, then rebuilds the precomputed
Postgres `agg_*` tables every read endpoint serves from (per-route / -stop /
-hour / -day rollups). Each run wipes the agency's `agg_*` rows and rewrites
them in one transaction from the *latest* `dep_delay` per stop event (what
passengers actually experienced), so routes that fall below the sample-count
cutoffs drop out rather than going stale.

---

## API

### Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Liveness check |
| `GET` | `/api/agencies` | List agencies |
| `POST` | `/api/agencies` | Register agency |
| `GET` | `/api/agencies/{id}` | Get agency |
| `POST` | `/api/{agency_id}/ask` | Natural-language question → Japanese answer (LLM fallback; also handles anonymous `__build__` sentinel from the dock) |
| `GET` | `/api/{agency_id}/ask/suggest` | Autocomplete suggestions for the empty/typing input |
| `GET` | `/api/{agency_id}/ask/build-schema` | Dock's parameterized-question schema |
| `POST` | `/api/{agency_id}/ask/edit-action` | Records confirm-vs-edit of a low-confidence canonical interpretation |
| `GET` | `/api/{agency_id}/ask/followup-enabled` | Returns `{enabled: bool}` — frontend uses this to gate the follow-up chip row |
| `GET` | `/api/{agency_id}/ask/dashboard/heatmap` | Route × DOW or hour-band delay heatmap |
| `GET` | `/api/{agency_id}/ask/dashboard/anomalies` | Daily-delay series with ±σ band + anomaly markers |
| `GET` | `/api/{agency_id}/ask/dashboard/movers` | Week-over-week largest delay movers |
| `GET` | `/api/{agency_id}/conversations` | List the caller's threads |
| `POST` | `/api/{agency_id}/conversations` | Create a thread (title + filter_ctx) |
| `GET` | `/api/{agency_id}/conversations/{cid}` | Get a thread + its messages |
| `PATCH` | `/api/{agency_id}/conversations/{cid}` | Update title / pinned / filter_ctx |
| `DELETE` | `/api/{agency_id}/conversations/{cid}` | Soft-delete a thread |
| `POST` | `/api/{agency_id}/conversations/{cid}/messages` | Deterministic dispatch — primary Ask path |
| `POST` | `/api/{agency_id}/conversations/{cid}/followup` | LLM-grounded follow-up bounded to the prior result (kill-switch gated) |
| `GET` | `/api/{agency_id}/conversations/{cid}/messages` | List messages in a thread |
| `POST` | `/api/{agency_id}/conversations/migrate-anon` | Bulk-import anon threads on first login |
| `GET` | `/api/{agency_id}/reports` | List pre-computed reports |
| `GET` | `/api/{agency_id}/reports/{type}` | Report payload (`format=json` default, `csv` for download) |
| `GET` | `/api/{agency_id}/delays/live` | Latest delay per trip |
| `GET` | `/api/{agency_id}/delays/heatmap` | GeoJSON delay heatmap by stop (range/DOW/time-band/route filtered) |
| `GET` | `/api/{agency_id}/route-shape` | Stop sequence + per-stop avg delay for one route (powers the map polyline) |
| `GET` | `/api/{agency_id}/today/route-summary` | Per-route triage for the latest observation date: today vs historical baseline deviation + severity bucket (anomaly/watch/normal/no_baseline) |
| `GET` | `/api/{agency_id}/today/route/{route_code}/trips` | Drilldown ①: per-trip avg delay for one route on the latest day (worst first) |
| `GET` | `/api/{agency_id}/today/route/{route_code}/stop-profile` | Drilldown ②: per-stop-sequence avg delay along one route (where delay builds) |
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
  -H "Origin: http://localhost:8000" \
  -d '{"question": "系統5の遅延は？"}'
```

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

Single-page React app at `frontend/` (React 19.2 + React Compiler, Vite 7, TypeScript strict, TanStack Query, react-router-dom, MapLibre GL, react-i18next). Tabs: Overview / Map / Ask / Live (最新観測 — the triage tab) / Reports. Default route is the Ask tab. UI chrome is bilingual (ja / en) via locale switcher in the header.

Platform notes (since the React 19 modernization, PRs #43/#46/#45):

- **Code splitting** — every tab/page route is `React.lazy`; MapLibre (~800 KB) loads only when the Map tab is visited. Entry chunk ≈ 440 KB. A router-level `RouteError` boundary degrades render crashes to an inline message instead of a white screen.
- **React Compiler** is on (`babel-plugin-react-compiler` in `vite.config.ts`). Don't add `useMemo`/`useCallback`/`React.memo` for performance — the compiler memoizes automatically. Existing manual memoization is harmless and pruned opportunistically.
- **Compiler lint** — `eslint-plugin-react-hooks` v7 `recommended-latest`. Two rules are staged at `warn` for pre-existing code (`set-state-in-effect`, `purity`); new code must keep them clean. Handlers that need fresh props inside once-registered listeners use `useEffectEvent` (see `MapTab`), not render-time ref mirroring.
- **Request cancellation** — all GET hooks thread TanStack Query's `AbortSignal` into `apiGet`; filter changes abort in-flight requests.
- **i18n lints** — `npm run lint:i18n` checks ja/en key parity; `npm run lint:i18n-strings` fails on hardcoded kana in `src/` (comment-only lines and `.test.` files are skipped; suppress legitimate cases with `i18n-ignore`).

Key components (all under `frontend/src/components/`):

- `RangeBadge` + `TabFilterBar` + `FilterContextBar` — unified date-range / DOW / time-band / service / route filter strip; state lives in URL params and persists across tab switches.
- `DataStalenessBanner` — calm warm-tan pill above the tab content when GTFS ingest hasn't run in 24h+. Reuses the existing `/today/route-summary` query (zero extra requests).
- `MapLegend` — draggable, position-persisted overlay explaining the delay-severity color ramp.
- `charts/DailyChart` — sample-weighted line chart for trend reports.
- `charts/HourlyHeatmap` — date × hour-of-day heatmap; click a row label / column / cell to drill the global filter into that time-band / day / both.
- `ReportTable` — inline horizontal bars colored by severity for ranking/compare reports; CSV export with Japanese headers.
- Map tooltips show stop name, GTFS `platform_code` (のりば badge), `stop_code`, contributing route_codes, and the active filter period.

Ask-tab specifics:

- `tabs/AskTab.tsx` — thread/filter state owner: left thread sidebar, top filter pill, middle scroll area, bottom `QuestionDock`. The visible filter is *derived* (unsaved edit → conversation's stored `filter_ctx` → URL range), and an in-flight filter save is awaited before dispatch so answers never use a stale scope.
- `tabs/ask/` — presentational pieces: `MessageList`/`Bubble`, `RichResult` (table / kv / chart per tool-result kind), `FollowupChipsRow`, and the `FilterCtx` helpers.
- `components/QuestionDock.tsx` — owns the dock state machine (idle / composing / busy); renders the chip row and rises a `ParamStrip` when a chip is tapped.
- `components/ParamStrip.tsx` — one-row inline parameter composer per `CardTemplate`; routes each `ParamSpec` to its pill control.
- `components/paramPills/{SegmentedPill,LimitPill,RoutePickerPill}.tsx` — small popover-style param controls with Escape-to-close, focus restoration, and outside-click dismiss.
- `components/ThreadSidebar.tsx` — ChatGPT-style conversation list with anonymous (localStorage) → authed (server) migration on first login.
- `components/askCardTemplates.ts` + `askFollowupChips.ts` — declarative chip templates; single source of truth for tool + args + i18n title keys.

### Local dev

```bash
make frontend-install     # one-time: install npm deps
make serve                # in one shell — FastAPI on :8000
make frontend-dev         # in another  — Vite dev server on :5173
```

The dev server proxies `/api` and `/health` to FastAPI; everything else is owned by the SPA, so direct reloads on `/agencies/:id/map` etc. work in dev. `http://localhost:5173` is already in the default CORS + CSRF allow lists.

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

### Runtime & data flow

How data gets from the road to the dashboard, and where each piece runs:

```mermaid
flowchart TD
    feeds["GTFS-RT feeds<br/>4 agencies"]

    subgraph ORACLE["Oracle Cloud VM — collector"]
        collect["Poll every ~30s ·<br/>roll daily per-agency<br/>archive zips + static GTFS"]
    end

    subgraph R2["Cloudflare R2 — object storage"]
        bucket["Daily archive zips<br/>durable raw · lifecycle-pruned"]
    end

    subgraph RAILWAY["Railway project · private network"]
        job["Daily ingest job · cron 1×/day<br/>ingest → analyze_all → prune"]
        ch["ClickHouse · MergeTree<br/>raw: updates (~575M rows, 4 agencies)<br/>sort key: agency_id, captured_at, route_code, ..."]
        subgraph DB["Postgres · PostGIS + pgvector"]
            static["static_* tables"]
            agg["precomputed: agg_* tables"]
        end
        app["App container · one image<br/>FastAPI API + React SPA, same origin<br/>reads agg_* (sub-second)<br/>falls back to ClickHouse for time_band-filtered queries"]
    end

    LLM["LLM providers<br/>Cerebras / Groq"]
    user["Browser<br/>map · charts · Ask"]

    feeds -->|HTTP GET| collect
    collect -->|daily upload| bucket
    bucket -->|daily pull · HTTPS| job
    feeds -.->|"fallback: ingest_live (live sample)"| job
    job -->|write| ch
    ch -->|analyze dedups + rebuilds| agg
    agg --> app
    ch -.->|"live fallback (time_band filter)"| app
    app -->|HTTPS · TLS at Railway edge| user
    app -.->|Ask stage 3 only| LLM
```

Five things this encodes:

- **Read endpoints almost never scan raw data.** Every API response comes from
  the precomputed `agg_*` tables (sub-second); `analyze` rebuilds them after
  each ingest. The one exception is a `time_band`-filtered ("slow path") query
  — those fall back to a live ClickHouse scan, since `agg_*` doesn't carry an
  hour-of-day column.
- **Raw `updates` lives in ClickHouse, not Postgres.** A columnar MergeTree
  table gives better compression and scan throughput at ~575M-row scale than
  Postgres did; Postgres keeps `static_*`/`agg_*`/OLTP/PostGIS/pgvector, none
  of which benefit from a columnar store. `analyze` reads ClickHouse and
  writes `agg_*` back into Postgres — every downstream aggregate-builder query
  is otherwise unchanged.
- **The database is private.** Ingest, analyze, and backups all run *inside* the
  Railway project — nothing external connects to Postgres or ClickHouse.
- **Oracle is the primary source, `ingest_live` is the fallback.** The dense 30s
  Oracle archive (via R2) feeds production; a direct live-feed sample is the
  no-Oracle backup path.
- **The LLM is off the hot path.** Ask answers from deterministic SQL tools
  (stages 1–2); only the stage-3 RAG fallback calls a provider.

### Module map

<details>
<summary>Per-file responsibilities</summary>

```
GTFS-RT .pb files / live feed_url
    │
    ▼
pipeline/ingest.py          zero-dependency protobuf parser → updates table (ClickHouse)
pipeline/clickhouse.py      sync ClickHouse client + dedup/insert helpers
pipeline/static_loader.py   GTFS Static zip → static_* tables (Postgres)
pipeline/analyze.py         reads ClickHouse updates, dedups, writes agg_* (Postgres)
    │
    ▼
api/main.py                 FastAPI app (asyncpg pool, Asia/Tokyo session, SPA static fallback)
api/middleware/
  auth.py                   X-API-Key validation → request.state.tier (free / pro)
  ratelimit.py              slowapi 60/min free, 600/min pro
  session.py                sid cookie → DB lookup → request.state.user (1/min last_seen throttle)
api/routers/
  agencies.py               agency CRUD
  ask.py                    /ask LLM fallback + /ask/suggest + /ask/build-schema + /ask/edit-action
  conversations.py          /conversations CRUD + /messages (deterministic) + /followup (LLM-grounded, kill-switch gated)
  ask_dashboard.py          /ask/dashboard/{heatmap,anomalies,movers} — analytical previews
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
  chat.py                   LLM-fallback chat (provider ladder, __build__ sentinel short-circuit)
  llm_client.py             Cerebras → Groq → Ollama provider ladder, malformed tool-call recovery
  tools.py                  the deterministic tool implementations + alias map (on_time, trend, cmp_service)
  tool_queries.py           SQL helpers for tools.py (per-route DOW / compare / metadata)
  intent.py                 IntentSignature + canonicalize + signature_hash
  intent_cache.py           ask_intent_cache DAL + cache_outcome bookkeeping
  router.py                 Stage-1 rules router (regex → tool args)
  embeddings.py             e5-small wrapper for stage-2 nearest-neighbor router
  rag_index.py              rag_chunks builder + cosine NN lookup
  conversations.py          ask_conversations + ask_conversation_messages DAL
  followup.py               LLM-grounded follow-up (bounded; kill-switch gated)
  labels.py                 dow_label / time_label display helpers
  formatter.py              Python templates → Japanese text (reports endpoint)
pipeline/dashboard_queries.py  /ask/dashboard/* SQL — heatmap, anomalies, movers
pipeline/audit.py           one-row INSERT into login_events (caller owns the txn)
pipeline/reports.py         compute_* aggregations (cached via async_lru_cache)
pipeline/cache.py           bounded async LRU + TTL decorator
```

</details>

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

Run a specific test file. **Point `DATABASE_URL` at the throwaway test DB on
`:5544`, never the dev DB on `:5433`** — the test suite auto-migrates its
target, and the dev DB holds tens of millions of rows of real data (see `CLAUDE.md` ▸
Databases for the one-line container build):

```bash
DATABASE_URL=postgresql://transit:transit@localhost:5544/transit_test \
  GROQ_API_KEY=test-key \
  poetry run pytest tests/query/test_tool_queries.py -v
```

---

## Deployment (Railway)

Railway runs the two Docker images — the **app** (`Dockerfile`, with the SPA
baked in, serving API + UI on one origin) and the **database** (custom
`db/Dockerfile`: PostGIS + pgvector + pg_trgm, on a persistent volume) — with
free TLS, a managed domain, and auto-deploy on `git push`. ~$10–18/mo,
usage-based. No box to harden, no reverse proxy to run. The DB stays on the
private network; a **daily Railway scheduled job** ingests the day's Oracle
archives from object storage — no public DB, no always-on worker. `ingest_live`
(via the `CRON_SECRET`-gated `POST /internal/cron/ingest` endpoint) remains a
lower-fidelity fallback for when object storage isn't wired.

Full step-by-step (DB service → app service → data load → ingest job → custom
domain → backups): [`docs/deploy-railway.md`](docs/deploy-railway.md).
The app service config (Dockerfile builder, `/health` check, `migrate up`
pre-deploy command) is pinned in [`railway.json`](railway.json).

Domain ideas (portfolio): `transit-delay.app`, `gtfs-jp.dev`, `chien-map.app` (遅延 = delay), `jptransit.app`. Cheapest registrars for `.app`/`.dev` are Cloudflare Registrar (~$12–14/yr, no markup, free WHOIS privacy).

### Required env (set as Railway service Variables)

| Variable | Notes |
|---|---|
| `DATABASE_URL` | `postgresql://transit:<POSTGRES_PASSWORD>@db.railway.internal:5432/transit` — the private hostname of the db service |
| `GROQ_API_KEY` | from console.groq.com |
| `CRON_SECRET` | `openssl rand -hex 32`; must match the GH Actions repo secret of the same name |
| `POSTGRES_PASSWORD` | `openssl rand -hex 24`; set on the db service, reused in `DATABASE_URL` |
| `CORS_ORIGINS` | leave empty — SPA + API are same-origin |

`PORT` is injected by Railway automatically (the Dockerfile honours
`${PORT:-8000}`); don't set it yourself.

### Daily ingest job env (set on the `ingest` scheduled service)

| Name | Notes |
|---|---|
| `DATABASE_URL` | same private-host URL as the app service |
| `OBJECT_STORE_ENDPOINT` / `OBJECT_STORE_BUCKET` | S3-compatible store (Cloudflare R2 / AWS S3) Oracle uploads the daily zips to |
| `OBJECT_STORE_ACCESS_KEY_ID` / `OBJECT_STORE_SECRET_ACCESS_KEY` | read creds for the job, write creds on Oracle's upload step |
| `AGENCY_IDS` / `RETENTION_DAYS` | agencies to ingest; raw-row prune window (default 400d) |

`CRON_SECRET` is only needed if you also use the `ingest_live` fallback
endpoint — set it identically on the app service and on whatever external
scheduler pokes `POST /internal/cron/ingest`.

### Operational notes

- Migrations: auto-run. `railway.json`'s `preDeployCommand` runs
  `python gtfs_pipeline.py migrate up` before every release (idempotent —
  tracked in `schema_migrations`). No manual step on deploy.
- The daily ingest job is observable in its Railway service Logs. Manual
  replay: trigger the `ingest` service from the Railway dashboard.
- Production ingests the **Oracle archives** (dense 30s observations) pulled
  from object storage by the daily job — not a live feed sample. `ingest_live`
  is the no-Oracle fallback. `make fetch-ingest` (Oracle SSH replay) is the
  **local-dev** equivalent of the same archive path.
- The db service's **volume** (`/var/lib/postgresql/data`) is the only stateful piece — without it, data is wiped on every redeploy.

### Observability

Every HTTP response carries an `X-Request-Id` header. Clients can pass
their own `X-Request-Id` (alphanumeric + dash, ≤64 chars) — anything
that doesn't match gets replaced with a fresh UUID4 hex. Every request
emits one access-log line to the `api.access` logger:

```
2026-05-24T12:34:56.789Z INFO api.access request_id=8a1f… msg="method=GET path=/api/agencies status=200 duration_ms=12 user_id=-"
```

To debug an issue:
1. Grab the `X-Request-Id` from the client's error (or from the user
   reporting it — the SPA echoes it on every response).
2. Filter the app service's logs by `request_id=<value>` (Railway →
   app service → Logs, or `railway logs --service app | grep …`) to find
   every log line emitted during that request, including inner
   module-level loggers.

`LOG_LEVEL` env var (default `INFO`) sets the root level. Set to
`DEBUG` for verbose investigations.

`make serve` and the Docker entrypoint both pass `--no-access-log` so
uvicorn's default access line doesn't double up with the `api.access`
emission. Plan for ~150 B per access line; at 1 req/s that's ~13 MB/day
— Railway retains and rotates service logs for you.
