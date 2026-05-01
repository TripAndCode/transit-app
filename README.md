# Transit Delay App

Real-time bus delay analysis for Aomori City Bus (青森市バス). Ingests GTFS-RT protobuf feeds, aggregates delay statistics, and exposes them through a FastAPI REST + WebSocket API. Natural-language queries in Japanese are classified by a local Llama 3.2 model (via Ollama) and answered with deterministic SQL — no paid LLM API.

---

## Requirements

- Python 3.11+
- [Poetry](https://python-poetry.org/)
- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (for the PostgreSQL + PostGIS container)
- [Ollama](https://ollama.com/) with `llama3.2` pulled (for NL queries only)

---

## Quick Start

### 1. Start the database

```bash
docker run -d \
  --name transit-pg \
  -e POSTGRES_USER=transit \
  -e POSTGRES_PASSWORD=transit \
  -e POSTGRES_DB=transit \
  -p 5433:5432 \
  postgis/postgis:14-3.2
```

### 2. Apply schema

```bash
make schema
```

### 3. Install dependencies

```bash
make install
```

### 4. Set environment (optional)

Create `.env` in the project root — it is gitignored and loaded automatically by `make`:

```
DATABASE_URL=postgresql://transit:transit@localhost:5433/transit
```

The default value already matches the container above, so this step is only needed if you use different credentials.

---

## Pipeline

All commands go through `gtfs_pipeline.py`. `make` targets forward to it.

### Register an agency

```bash
DATABASE_URL=... poetry run python gtfs_pipeline.py add_agency \
  --name "青森市バス" \
  --feed-url "https://aomoricitybus.com/TripUpdate.pb"
```

### Ingest GTFS-RT archives

```bash
make ingest FOLDER=./raw_archives
# with explicit agency when multiple exist:
make ingest FOLDER=./raw_archives AGENCY_ID=1
```

Reads `.pb` files from tarballs and loose files. Deduplication key is `{date_dir}/{pb_name}` — delete the database and re-ingest from scratch if you need to reprocess.

### Load static GTFS

```bash
make load_static PATH=./raw_archives_static
```

Loads `stops.txt`, `stop_times.txt`, `trips.txt`, `routes.txt`, `calendar_dates.txt` from the latest `*_static.zip` in the given directory. Required for stop-name resolution and map heatmap.

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

## API Server

```bash
make serve          # default port 8000
make serve PORT=9000
```

Interactive docs: `http://localhost:8000/docs`

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

### Ask endpoint

```bash
curl -X POST http://localhost:8000/api/1/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "系統44372の遅延は？"}'
```

Query types understood: `ranking`, `by_hour`, `by_dow`, `by_stop`, `by_date`, `trend`, `on_time`, `compare`, `worst_5min`, `stop_ranking`, `dow_ranking`, `compare_ranking`, `stop_list`, `routes_at_stop`, `route_info`, `timetable`.

---

## Architecture

```
GTFS-RT .pb files
    │
    ▼
pipeline/ingest.py          zero-dependency protobuf parser → updates table
pipeline/static_loader.py   GTFS Static zip → static_* tables
pipeline/analyze.py         SQL aggregations → agg_* tables
    │
    ▼
api/main.py                 FastAPI app (asyncpg connection pool)
api/routers/
  agencies.py               CRUD for agencies
  ask.py                    NL question → intent → execute → format
  query.py                  structured intent → execute → format
  ws.py                     WebSocket chat
  map.py                    live delays + GeoJSON heatmap
  static.py                 route/stop lists
    │
    ▼
pipeline/query/
  intent.py                 classify_intent() via Ollama llama3.2 JSON mode
  executor.py               16 async SQL executors (asyncpg, $N params)
  formatter.py              Python templates → Japanese text
```

**Trip ID format:** `{service_type}_{HH}時{MM}分_系統{route_code}`  
Example: `平日_11時37分_系統44372` → service=平日, time=11:37, route=44372

**Delays** are stored in seconds in `updates.dep_delay` and reported in minutes everywhere else.

---

## Development

```bash
make fmt       # ruff format
make lint      # ruff check
make test      # pytest (requires DATABASE_URL / running container)
make check     # fmt + lint + test
```

Run a specific test file:

```bash
DATABASE_URL=postgresql://transit:transit@localhost:5433/transit \
  poetry run pytest tests/test_executor.py -v
```
