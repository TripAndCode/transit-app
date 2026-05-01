# Transit App — Production Portfolio Design

**Date:** 2026-05-01  
**Goal:** Transform the Aomori-specific transit delay app into a publicly hosted, portfolio-quality platform supporting any Japanese GTFS-JP agency, with a React frontend, Groq-powered NL queries, Railway deployment, and passive monetization via AdSense.

---

## 1. Context & Constraints

- **Audience:** Personal portfolio — primary payoff is employment/freelance signal, secondary is ad revenue
- **Hosting:** Railway (~$5/mo hobby plan, always-on, GitHub-connected deploys)
- **LLM:** Groq free tier (llama3.2 — same model as current Ollama, drop-in replacement)
- **Frontend:** React + Tailwind + Leaflet
- **Scope:** Any Japanese GTFS-JP operator; Japan-specific vocabulary (`平日/土日祝`, `系統`) stays as-is since it is GTFS-JP standard, not Aomori-specific
- **Monetization:** Google AdSense in frontend footer; Pro API key tier (rate-limit lifted) via future Stripe integration

---

## 2. Architecture

```
frontend/                        React + Tailwind + Leaflet
  src/
    pages/Home.tsx               Agency selector, top-delay ranking
    pages/Map.tsx                Live heatmap + delay markers (Leaflet)
    pages/Chat.tsx               NL chat (WebSocket) + intent JSON pane
    components/                  Shared UI components
  → built to frontend/dist/
  → deployed as Railway static service

api/                             FastAPI (existing routers unchanged)
  middleware/
    auth.py                      X-API-Key header validation
    ratelimit.py                 60 req/min per IP (free), unlimited (pro)
  main.py                        CORS locked to Railway domain

pipeline/
  ingest.py                      trip_id parser made configurable per agency
  query/intent.py                ollama → groq SDK

db/schema.sql                    + api_keys table, + trip_id_pattern column

.github/workflows/ci.yml         ruff + pytest on PR; Railway deploy on push to main
Dockerfile                       FastAPI service container
railway.toml                     Two services: api + frontend; Postgres; cron jobs
```

**Deployment topology:** one Railway project, two services (`api`, `frontend`), one managed Railway Postgres. Secrets (`GROQ_API_KEY`, `DATABASE_URL`) injected as Railway env vars.

---

## 3. Generalization Strategy

The only Aomori-specific code that blocks other agencies is `parse_trip_id()` in `pipeline/ingest.py`. All Japanese vocabulary (`平日/土日祝`, `系統`, the intent prompt) is GTFS-JP standard.

### Schema change

```sql
ALTER TABLE agencies ADD COLUMN trip_id_pattern TEXT;
-- NULL = use built-in default (current Aomori regex)
```

### Code change

```python
# pipeline/ingest.py
_TRIP_RE_DEFAULT = re.compile(
    r"^(?P<service>.+?)_(?P<hour>\d+)時(?P<minute>\d+)分_系統(?P<route>\d+)$"
)

def parse_trip_id(trip_id: str, pattern: re.Pattern = _TRIP_RE_DEFAULT):
    m = pattern.match(trip_id)
    ...
```

At ingest time, load the agency's `trip_id_pattern` from the DB; compile it once and pass it through. Agencies that match the Aomori pattern need no configuration.

---

## 4. AI Layer — Ollama → Groq

Replace the `ollama` SDK with the `groq` SDK in two files:

**`pipeline/query/intent.py` — `classify_intent()`:**

```python
from groq import Groq
client = Groq(api_key=os.environ["GROQ_API_KEY"])

response = client.chat.completions.create(
    model="llama-3.2-11b-text-preview",  # free tier; 90b is rate-limited/paid
    messages=[...],
    response_format={"type": "json_object"},
    temperature=0,
)
content = response.choices[0].message.content
```

**`pipeline/query/formatter.py` — `format_unknown()` (streaming):**

```python
stream = client.chat.completions.create(..., stream=True)
result = "".join(chunk.choices[0].delta.content or "" for chunk in stream)
```

No changes to prompts, `validate_intent()`, or any executor/formatter logic.

---

## 5. Frontend — 3 Pages

### Home (`/`)
- Agency dropdown populated from `GET /agencies`
- Top-10 delay ranking table for the selected agency
- Link to `/docs` (OpenAPI) and a "Get API Key" CTA
- AdSense leaderboard in footer

### Map (`/map`)
- Full-screen Leaflet map
- Heatmap layer: `GET /api/{id}/delays/heatmap` (GeoJSON — already exists)
- Live delay markers: `GET /api/{id}/delays/live`
- Agency switcher top-left corner

### Chat (`/chat`)
- Left pane: chat input + message history, wired to `WS /api/{id}/chat`
- Right pane: structured intent JSON returned with each answer (shows off the AI pipeline to technical visitors)
- Agency switcher in header

---

## 6. Auth & Rate Limiting

### New DB table

```sql
CREATE TABLE api_keys (
    key         TEXT PRIMARY KEY,
    owner_email TEXT NOT NULL,
    tier        TEXT NOT NULL DEFAULT 'pro',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### Middleware behaviour

- Requests without `X-API-Key`: rate-limited at 60 req/min per IP via `slowapi`
- Requests with a valid pro key: rate limit bypassed
- Invalid key: 401

### Future Stripe path

Stripe checkout session → webhook → `INSERT INTO api_keys`. No Stripe work needed in this iteration; the table and middleware are the foundation.

---

## 7. CI/CD

**`.github/workflows/ci.yml`:**

```yaml
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgis/postgis:14-3.2
        env: { POSTGRES_USER: transit, POSTGRES_PASSWORD: transit, POSTGRES_DB: transit }
        ports: ["5433:5432"]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install poetry && poetry install
      - run: poetry run ruff check . && poetry run ruff format --check .
      - run: DATABASE_URL=postgresql://transit:transit@localhost:5433/transit poetry run pytest
  deploy:
    needs: test
    if: github.ref == 'refs/heads/main'
    uses: Railway GitHub integration (auto-triggered by push to main)
```

**`railway.toml`:** defines `api` and `frontend` services + two cron jobs:

```toml
[services.api]
source = "."
build_command = "docker build -t api ."
start_command = "uvicorn api.main:app --host 0.0.0.0 --port $PORT"

[services.frontend]
source = "frontend"
build_command = "npm run build"
publish_dir = "dist"

[[cron]]
name = "ingest"
schedule = "*/15 * * * *"   # every 15 min
command = "python gtfs_pipeline.py ingest_live"
# NOTE: ingest_live is a NEW subcommand to implement — fetches feed_url from
# each agency row, downloads the latest GTFS-RT protobuf, and ingests it.
# Replaces the current manual `make ingest FOLDER=...` workflow.

[[cron]]
name = "analyze"
schedule = "0 * * * *"      # every hour
command = "python gtfs_pipeline.py analyze"
```

---

## 8. Monetization

| Source | Implementation | Expected |
|---|---|---|
| Google AdSense | `<script>` in `index.html`, one leaderboard slot in footer | Passive, low initially |
| Pro API tier | `api_keys` table + `X-API-Key` middleware (already designed) | Future Stripe integration |
| Portfolio signal | App itself → job offers / freelance contracts | Primary realistic payoff |

---

## 9. Scope Summary — What Changes vs What Stays

| | Status |
|---|---|
| All existing routers (`ask`, `query`, `ws`, `map`, `static`, `agencies`) | Unchanged |
| All 16 SQL executors in `executor.py` | Unchanged |
| All formatters in `formatter.py` | Unchanged |
| Intent system prompt + `validate_intent()` | Unchanged |
| DB schema (all existing tables) | Unchanged — additive only |
| `parse_trip_id()` in `ingest.py` | Small refactor: accepts `pattern` kwarg |
| `classify_intent()` + `format_unknown()` | ~10 lines each: ollama → groq |
| `api/main.py` | Add middleware, tighten CORS |
| `agencies` table | Add `trip_id_pattern` column |
| New: `api_keys` table | New |
| New: `api/middleware/auth.py` + `ratelimit.py` | New |
| New: `frontend/` | New (React + Tailwind + Leaflet) |
| New: `Dockerfile`, `railway.toml`, `.github/workflows/ci.yml` | New |
| New: `ingest_live` subcommand in `gtfs_pipeline.py` | New — fetches live `feed_url` per agency on a schedule; replaces manual folder-based ingest |
