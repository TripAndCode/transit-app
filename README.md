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

### Frontend dispatch surface (Phase ③ onward)

The user-facing Ask tab is a **chat-first QuestionDock** in
`frontend/src/components/QuestionDock.tsx`:

- Bottom-pinned strip of **5 question chips** — 🏆 Top-N delays, 🎯 On-time
  rate, 📈 Route delay trend, ⚖️ Weekday vs Weekend, 🚏 Route overview
- Tapping a chip raises a one-row **`ParamStrip`** above the chips with
  inline pills (件数 / 運行種別 / 路線 / 粒度 / 指標) and an 実行 button
- Submit hits `POST /api/{agencyId}/conversations/{cid}/messages` with
  `{tool, args, user_summary}` — **deterministic dispatch, zero LLM**.
  Backend `append_message_endpoint` (`api/routers/conversations.py`)
  canonicalizes args, looks up the canonical-intent cache, then runs
  `pipeline.query.tools.dispatch()` and persists user + assistant rows
  in one transaction
- Anonymous users (no login) use the same flow via
  `POST /api/{agencyId}/ask` with a `__build__ {tool} {args}` sentinel
  question — `pipeline/query/chat.py` short-circuits the LLM path and
  runs the same `dispatch()` directly. Threads live in `localStorage`
  until migrated on login

Each result-bearing assistant bubble gets a row of **follow-up chips**
(`why` / `reliability` / `slice` / `summarize` / `next`). Tapping one
hits `POST /conversations/{cid}/followup` with the prior result as
grounding context. The LLM is called *only* here, with no tool use, so
it's bounded to interpretation of data the user is already looking at.

| Flag | Default | Effect |
|---|---|---|
| `ASK_FOLLOWUP_ENABLED` | `false` | When off, follow-up chip row is hidden and `POST /followup` returns 503. Disable path for the LLM kill-switch. |
| `ASK_INTENT_CACHE_ENABLED` | `false` | Enables the canonical-intent cache + promotion job (see [Phase ②](#canonical-intent--cache-phase-) below). |
| `ASK_HISTORY_ENABLED` | `true` | When off, the LLM stage gets no conversation memory. |
| `ASK_QUERY_LOG_ENABLED` | `true` | When off, no rows are written to `ask_query_log`. |

### Free-text `/ask` (LLM fallback, also used by anonymous build-sentinel)

`POST /api/{agency_id}/ask` answers a Japanese (or English) natural-language
question about delay data. It uses a 3-stage router so most common questions
never reach the LLM, keeping the Cerebras/Groq free tiers comfortable. The
QuestionDock does not exercise this path for authed users; the deterministic
`conversations/messages` flow above is used instead. Anonymous users still
fall through here via the build-sentinel short-circuit.

### Request flow

```
                     POST /api/{agency_id}/ask
                                │
                                ▼
                  ┌─────────────────────────────┐
                  │ Stage 1: rules router       │  pipeline/query/router.py
                  │ regex/keyword → tool + args │
                  └──────┬──────────────────────┘
                         │ match?
                  yes ◄──┴──► no
                   │            │
                   │            ▼
                   │  ┌─────────────────────────────┐
                   │  │ Stage 2: embedding router   │  pipeline/query/embeddings.py
                   │  │ e5-small(Q) → top-1 in      │  pipeline/query/rag_index.py
                   │  │ rag_chunks; cosine sim > 85%│
                   │  └──────┬──────────────────────┘
                   │         │
                   │   yes ──┴── no
                   │     │        │
                   │     │        ▼
                   │     │   ┌─────────────────────────────┐
                   │     │   │ Stage 3: RAG-augmented LLM  │  pipeline/query/chat.py
                   │     │   │ top-3 nearest examples →    │
                   │     │   │ injected into system prompt │
                   │     │   │ → LLM picks tool            │
                   │     │   │ (Cerebras → Groq → Ollama)  │
                   │     │   └──────┬──────────────────────┘
                   │     │          │
                   ▼     ▼          ▼
            pipeline.query.tools.dispatch(...)
```

### The three stages

1. **Rules router** — ~25 hand-written regex/keyword patterns. `どんな路線` →
   `describe_data(kind=routes)`. `定時率TOP10` → `top_n(metric=on_time_rate, n=10)`.
   Hits without calling the LLM. Add a rule by editing `_RULES` in
   `pipeline/query/router.py` (PR-reviewed, restart required).

2. **Embedding router** — if no rule matches, embed the question with
   [`intfloat/multilingual-e5-small`](https://huggingface.co/intfloat/multilingual-e5-small)
   (loaded once at API startup, ~120MB, free), then nearest-neighbor against
   `rag_chunks` (cosine distance). When `cosine_sim > 0.85`, dispatch the
   matching golden question's tool directly. Still no LLM call.

3. **RAG-augmented LLM** — long-tail questions. Top-3 nearest golden examples
   are injected as few-shot into the system prompt before calling the LLM via
   the Phase 1.5 provider adapter (Cerebras → Groq → Ollama). The examples
   strongly bias the model toward the right tool even on novel phrasings.

### "Chunk" = one indexed question

`rag_chunks` stores **one row per question** from `tests/ask_eval/golden_set.jsonl`:

| Column | Example | Why |
|---|---|---|
| `chunk_id` | `meta-001` | matches `golden_set.jsonl` line id |
| `content` | `どんな路線がデータにあるの？` | canonical phrasing |
| `embedding` | `vector(384)` | e5-small output, pgvector-indexed |
| `content_hash` | `sha256(content)` | for idempotent rebuild |

Tool + args are NOT stored in `rag_chunks` — they live in `golden_set.jsonl`
(single source of truth), loaded into memory at API startup. Router looks up
the matched `chunk_id` to recover them.

### Building / rebuilding the index

`rag_chunks` is populated by a one-shot CLI command — not by a migration:

```bash
# one agency
poetry run python gtfs_pipeline.py build_rag_index --agency-id 1

# every agency in agencies table
make build-rag-index
```

Idempotent (re-running only re-embeds rows whose `question` text changed).
Run after `make bootstrap` for new environments, and after any change to
`golden_set.jsonl`.

### Graceful degradation

The router is **additive**. If any Phase 2 piece fails — model can't load,
`rag_chunks` is empty, pgvector errors — the request falls through to the
LLM path with no examples. Phase 1 behavior continues to work end-to-end.

Set `ASK_ROUTER_ENABLED=false` in `.env` to disable the router entirely at
runtime (no restart needed for in-flight requests; new requests pick up the
new value).

### Provider ladder & reliability

Stage 3 calls the LLM through an ordered, env-driven provider ladder
(`pipeline/query/llm_client.py`). Set `CHAT_PROVIDERS` to a comma list; each
provider needs its `*_API_KEY`. All are OpenAI-compatible, so one adapter
drives them:

| Provider | Model | Free tier |
|---|---|---|
| `cerebras` | `gpt-oss-120b` | 1,000,000 tok/day, 2,400 req/day, 5/min |
| `groq` | `llama-3.3-70b-versatile` | 100,000 tok/day |
| `ollama` (local only) | `qwen2.5:7b-instruct` | unmetered, ~5–15s/call |

Recommended: prod `CHAT_PROVIDERS=cerebras,groq`; local
`CHAT_PROVIDERS=cerebras,groq,ollama`. Ollama is a **local-only** fallback —
on a CPU box it's slow and a weaker tool-caller, so it's not advised as a prod
rung. Stages 1 and 2 use **no LLM at all**, so route lists, rankings, and stop
counts keep working even when every provider's quota is spent.

How the adapter hardens that call:

- **Malformed-tool-call recovery** — Groq intermittently returns a 400
  `tool_use_failed` where the model emitted its tool call as text. The adapter
  parses the attempted call out of `failed_generation` and dispatches it
  instead of failing over (the `json.loads` guard rejects any mis-parse).
- **Retry-once** on a transient connection/timeout error (same provider, no
  backoff) before descending the ladder.
- **Honest degradation** — when every provider is exhausted, the user-facing
  message depends on *why*: a quota exhaustion (429) steers them to the
  question types Stages 1–2 answer with no LLM; other failures show the
  generic retry message.

Local Ollama setup: `brew install ollama && ollama pull qwen2.5:7b-instruct`,
then append `,ollama` to `CHAT_PROVIDERS`.

### Follow-ups & conversation memory

The Ask tab is multi-turn. The frontend sends the **last 3 turns** in the
request; the server detects follow-up phrasings (もっと / 次の50件 / show me
more / 前のと逆順で) and routes them straight to the LLM stage with that
history attached, so the model continues from the prior turn — e.g.
「停留所はいくつ？」 (first 50) then 「次の50件」 → `describe_data(kind=stops,
offset=50)`. The list kinds (`routes` / `stops` / `sample_counts`) accept an
`offset` for pagination.

Memory is **client-supplied and ephemeral** — capped at 3 turns, gone on
reload. Anonymous and logged-in users behave identically; there is no
server-side conversation store. Set `ASK_HISTORY_ENABLED=false` to disable.

### Query analytics log

Every `/ask` writes one **anonymized** row to `ask_query_log`
(`question, router_stage, tool, agency_id, success, created_at`) — fire-and-
forget, never blocking the response. It has **no user id, session, or IP**: it
answers "what is asked and how well is it served", for router / golden-set
tuning and cost analysis (which questions burn the LLM). 90-day retention via
`make prune-query-log`. A free-text box can capture PII a user types; we
minimize (question + routing metadata only), do not link identity, and bound
exposure with retention. Set `ASK_QUERY_LOG_ENABLED=false` to disable.

Useful queries:

```sql
-- questions that fall to the LLM (candidates for new rules / golden entries)
SELECT question, count(*) FROM ask_query_log
WHERE router_stage = 'llm' GROUP BY 1 ORDER BY 2 DESC LIMIT 20;

-- unmet demand (failures)
SELECT question, count(*) FROM ask_query_log
WHERE success = false GROUP BY 1 ORDER BY 2 DESC LIMIT 20;
```

### Canonical intent + cache (Phase ②)

The blank text box is the root cause of "minor wording differences → different
answers." Phase ② shrinks that surface from two sides:

- **Codebase:** Stage 3's LLM call emits a strict JSON `IntentSignature`
  (`pipeline/query/intent.py`). We canonicalize the args (sort keys, lowercase
  enums, drop tool defaults, resolve relative dates), hash to a 16-char
  `signature_hash`, and look the hash up in `ask_intent_cache`. Two paraphrased
  questions with the same canonical intent share a row — the dispatch is
  deterministic regardless of wording, and a future job auto-promotes
  recurring signatures into `rag_chunks` so Stage 2 catches them next time
  (the LLM is skipped entirely).
- **UX:** Suggestion chips when the input is empty, autocomplete from the
  golden set as the user types, a 💬/🛠 mode toggle that swaps the chat input
  for a structured builder (zero LLM), and a confidence pill above each answer
  that surfaces low-confidence interpretations with a "違う？" link to re-run
  via the builder.

Toggle with the env var:

```bash
ASK_INTENT_CACHE_ENABLED=true    # full pipeline + guided UX
ASK_INTENT_CACHE_ENABLED=false   # default — Phase ① behaviour, no cache
```

The flag also gates `signature_hash` + `cache_outcome` columns on
`ask_query_log` writes (NULL when off). Useful cache-side queries once enabled:

```sql
-- top signatures (good promotion candidates)
SELECT signature_hash, tool, hit_count, last_question
FROM ask_intent_cache ORDER BY hit_count DESC LIMIT 20;

-- cache hit rate
SELECT cache_outcome, count(*) FROM ask_query_log
WHERE router_stage = 'llm' GROUP BY 1;
```

### Load data

`make bootstrap` doesn't pull any GTFS-RT data — the DB is empty until
you choose a load path:

```bash
# Path A — live fetch from each agency's official feed_url
poetry run python gtfs_pipeline.py ingest_live
make analyze

# Path B — replay archives from the Oracle Cloud collection VM (local dev only)
make fetch-ingest    # rsync + ingest + load_static + analyze in one shot
```

Path A is what production uses (the hourly cron hits
`POST /internal/cron/ingest`). Path B is local-only — see
[Path A vs Path B](#data-ingest-path-a-vs-path-b) for the why.

See [Deployment](#deployment-railway) for how the deployed cron is wired.

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

### Reset / re-bootstrap

```bash
make db-down                           # stop container, KEEP volume
docker compose down -v                  # stop and DELETE the data volume
make bootstrap                          # bring everything back up
```

### Quickstart cheat sheet

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
[Deployment](#deployment-railway) below.

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
| `0011_correctness_types` | `scheduled_time` TEXT→TIME (`updates` + `agg_route_hour`); `agg_route_dow.dow` TEXT→SMALLINT (ISODOW) |
| `0012_pgtrgm_route_names` | `pg_trgm` extension + trigram GIN indexes on `route_long_name` / `route_short_name` for fuzzy search |
| `0013_ask_query_log` | `ask_query_log` table for anonymized query analytics |
| `0014_ask_intent_cache` | `ask_intent_cache` table for the canonical-intent cache (Phase ②) |
| `0015_ask_intent_cache_composite_pk` | Composite PK on `(agency_id, signature_hash)` for per-agency cache isolation |
| `0016_ask_conversations` | `ask_conversations` + `ask_conversation_messages` for threaded Q&A (Phase ③) |
| `0017_agg_stop_daily` | `agg_stop_daily` + `agg_stop_routes` — per-stop, per-day delay for the map heatmap |
| `0018_agg_route_daily` | `agg_route_daily` — per-route, per-day summary for the fast `today/route-summary` |
| `0019_agg_route_daily_dist` | `agg_route_daily_dist` — per-day delay distribution (histogram) for range-scoped reports |
| `0020_agg_hour_daily` | `agg_hour_daily` — per-day, per-hour-of-day delay for Overview peak-hour-by-DOW |

> **2026-05-22 note on type changes:** migration `0011` retypes
> `scheduled_time` from `TEXT` to `TIME` and `agg_route_dow.dow` from
> Japanese-char `TEXT` to `SMALLINT` (ISODOW: Mon=1..Sun=7). After
> `make migrate`, run `make analyze` (or wait one hour for the cron
> tick) to repopulate `agg_*` rows under the new types. Ingest
> strategies skip rows with `scheduled_time` hour >= 24 or minute >= 60
> with a structured warning log — defensive guards against any feed
> producing values the strict TIME column can't hold.
>
> The migration normalises any pre-existing empty-string
> `scheduled_time` rows to `NULL` (`UPDATE … = NULL WHERE … = ''`)
> before the type cast, so operators upgrading a long-running database
> don't see the ALTER abort on legacy data. The `/api/{agency_id}/delays/live`
> JSON now returns `scheduled_time` as `"HH:MM:SS"` consistently (it
> used to be a mix of `"HH:MM"` and `"HH:MM:SS"` depending on the
> source strategy).

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

Fetches the current GTFS-RT protobuf from each agency's `feed_url`. In production it is invoked hourly by a GitHub Actions workflow that hits the guarded `POST /internal/cron/ingest` endpoint — see [Deployment](#deployment-railway).

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

> **2026-05-22 note on delay semantics:** report numbers from this date
> onward use the *most recent* `dep_delay` observation per stop event
> (latest by `captured_at`, with `id DESC` as a deterministic tiebreaker),
> not the maximum across the polling window. This matches what passengers
> actually experienced — GTFS-RT estimates refine as the trip nears each
> stop. Average delays may shift slightly downward on noisy feeds. Each
> `make analyze` (or hourly cron tick) wipes the agency's five `agg_*`
> tables and rewrites them from the freshly computed SELECTs in one
> transaction — routes whose data no longer meets the sample-count
> cutoffs disappear from the tables on the next run, rather than
> retaining stale values. Rows with `dep_delay IS NULL` are filtered at
> the inner dedup SELECT, so the "latest" is the latest *numeric*
> estimate per stop event. There is no rollback flag — to revert, revert
> the PR and re-analyze.

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
| `GET` | `/api/{agency_id}/ask/suggest` | Autocomplete suggestions for the empty/typing input (Phase ②) |
| `GET` | `/api/{agency_id}/ask/build-schema` | Dock's parameterized-question schema |
| `POST` | `/api/{agency_id}/ask/edit-action` | Records confirm-vs-edit of a low-confidence canonical interpretation |
| `GET` | `/api/{agency_id}/ask/followup-enabled` | Returns `{enabled: bool}` — frontend uses this to gate the follow-up chip row |
| `GET` | `/api/{agency_id}/ask/dashboard/heatmap` | Route × DOW or hour-band delay heatmap (Phase ③.5; remains for plotting) |
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

Ask-tab specifics (Phase ③ → ③.9):

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
  intent.py                 IntentSignature + canonicalize + signature_hash (Phase ②)
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
target, and the dev DB holds ~1.8M rows of real data (see `CLAUDE.md` ▸
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
usage-based. No box to harden, no reverse proxy to run. Cron is a GitHub
Actions workflow that hits a guarded `POST /internal/cron/ingest` hourly — no
always-on cron worker.

Full step-by-step (DB service → app service → data load → cron → custom
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

### Required GitHub repo secrets

| Name | Notes |
|---|---|
| `CRON_SECRET` | random 32 bytes; same value as `CRON_SECRET` in the app's Railway Variables |
| `APP_BASE_URL` | the Railway domain, e.g. `https://<app>.up.railway.app` (the GH workflow `curl`s `${APP_BASE_URL}/internal/cron/ingest`) |

### Operational notes

- Migrations: auto-run. `railway.json`'s `preDeployCommand` runs
  `python gtfs_pipeline.py migrate up` before every release (idempotent —
  tracked in `schema_migrations`). No manual step on deploy.
- The hourly cron is observable in the GitHub Actions tab. Manual replay: `gh workflow run "Hourly Ingest"`.
- `make fetch-ingest` (Oracle SSH replay) is **local-dev only** and not part of any deployed cron path. The deployed cron uses `ingest_live` — direct HTTPS GET of each agency's `feed_url`.
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
