# Transit Delay App

Real-time bus delay analysis for Japanese transit agencies. Ingests GTFS-RT protobuf feeds, aggregates delay statistics, and exposes them through a FastAPI REST + WebSocket API. Natural-language queries in Japanese are classified by Groq (llama-3.2-11b) and answered with deterministic SQL.

---

## Requirements

- Python 3.12+
- [Poetry](https://python-poetry.org/)
- [Docker Desktop](https://www.docker.com/products/docker-desktop/)
- A [Groq API key](https://console.groq.com/) (free tier is sufficient)

---

## Quick Start

### 1. Start the database

```bash
make db
```

Builds a PostGIS 14 + pgvector image, starts a `transit-pg` container on port 5433, and applies the schema. Everything lives inside this project — no external setup needed.

### 2. Install dependencies

```bash
make install
```

### 3. Set environment

Create `.env` in the project root (gitignored, loaded automatically by `make`):

```
DATABASE_URL=postgresql://transit:transit@localhost:5433/transit
GROQ_API_KEY=your_groq_api_key_here
```

### 4. Register an agency

```bash
poetry run python gtfs_pipeline.py add_agency \
  --name "青森市バス" \
  --feed-url "https://example.com/TripUpdate.pb"
```

Any Japanese GTFS-JP operator is supported. If the agency uses a non-standard `trip_id` format, add a `trip_id_pattern` regex:

```bash
poetry run python gtfs_pipeline.py add_agency \
  --name "My Agency" \
  --feed-url "https://..." \
  --trip-id-pattern "^(?P<service>.+?)_(?P<hour>\d+)h(?P<minute>\d+)_route(?P<route>\d+)$"
```

### 5. Load data

```bash
# Ingest GTFS-RT archives (historical .pb files)
make ingest FOLDER=./raw_archives

# Load static GTFS (stops, routes, timetable)
make load_static PATH=./raw_archives_static

# Run aggregations
make analyze
```

### 6. Start the server

```bash
make serve          # port 8000
make serve PORT=9000
```

Interactive docs: `http://localhost:8000/docs`

---

## Database

| Command | Effect |
|---|---|
| `make db` | Build image, start container, apply schema |
| `make db-down` | Stop container (data volume preserved) |
| `make schema` | Re-apply schema to a running container |
| `docker compose down -v` | Stop and delete data volume |

Data is stored in a named Docker volume (`prod-backend_transit_pgdata`) — it survives container restarts.

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

Fetches the current GTFS-RT protobuf from each agency's `feed_url`. Runs automatically on Railway every 15 minutes via cron.

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
| `GET` | `/agencies` | List agencies |
| `POST` | `/agencies` | Register agency |
| `GET` | `/agencies/{id}` | Get agency |
| `POST` | `/api/{agency_id}/ask` | Natural-language question → Japanese answer |
| `POST` | `/api/{agency_id}/query` | Structured intent → rows + answer |
| `WS` | `/api/{agency_id}/chat` | WebSocket chat session |
| `GET` | `/api/{agency_id}/delays/live` | Latest delay per trip |
| `GET` | `/api/{agency_id}/delays/heatmap` | GeoJSON delay heatmap by stop |
| `GET` | `/api/{agency_id}/routes` | Static route list |
| `GET` | `/api/{agency_id}/stops` | Static stop list |

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
api/main.py                 FastAPI app (asyncpg pool, auth + rate-limit middleware)
api/middleware/
  auth.py                   X-API-Key validation → request.state.tier
  ratelimit.py              slowapi 60/min free, 600/min pro
api/routers/
  agencies.py               agency CRUD
  ask.py                    NL question → intent → execute → format
  query.py                  structured intent → execute → format
  ws.py                     WebSocket chat
  map.py                    live delays + GeoJSON heatmap
  static.py                 route/stop lists
    │
    ▼
pipeline/query/
  intent.py                 classify_intent() via Groq JSON mode
  executor.py               16 async SQL executors (asyncpg, $N params)
  formatter.py              Python templates → Japanese text
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

## Deployment (Railway)

The app is configured for Railway via `railway.toml` and `Dockerfile`. Set these environment variables in your Railway service:

| Variable | Description |
|---|---|
| `DATABASE_URL` | Railway Postgres connection string (injected automatically) |
| `GROQ_API_KEY` | Your Groq API key |
| `CORS_ORIGINS` | Comma-separated allowed origins (e.g. `https://your-frontend.up.railway.app`) |
