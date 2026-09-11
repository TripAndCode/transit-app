# Weather station registration runbook

Operator runbook for turning on the rain-vs-dry delay comparison
(`/weather_delay`, see `pipeline/reports/weather.py`) for one agency. This is a
manual, doc-only procedure: `agency_weather_stations` is deliberately
hand-populated by an operator, the same convention as `ridership_weights`
(migration 0035) and `route_performance_standards` (migration 0041) — see the
rationale in `db/migrations/0042_weather_observations.up.sql`. No agent or
automated pipeline writes this table, and this document does not either; it
only describes the SQL an operator runs by hand against the real database.

## Why this is a manual step

Picking the station that represents an agency's service area is a judgement
call about geography and station siting, not something derivable from GTFS —
an agency spanning a mountain pass may want the inland station even though its
depot sits nearer a coastal one. An agency with no row in
`agency_weather_stations` gets no weather metric at all (`/weather_delay`
returns `available: false`), never a silently guessed nearest station.

## Step 1 — find the JMA AMeDAS station id

JMA (気象庁) publishes the full AMeDAS station list at
`https://www.jma.go.jp/bosai/amedas/const/amedastable.json`. It is a single
JSON object keyed by 5-digit station id; each entry looks like:

```json
"31312": {
  "type": "A",
  "elems": "11111111",
  "lat": [40, 49.3],
  "lon": [140, 46.1],
  "alt": 3,
  "kjName": "青森",
  "knName": "アオモリ",
  "enName": "Aomori"
}
```

To find the right station:

1. Fetch the JSON and search it by `kjName` (the station's kanji name) for
   the city/town nearest the agency's service area.
2. The matching object's top-level key (e.g. `31312`) is the station id —
   this is what goes in `agency_weather_stations.station_id`, and it must be
   exactly 5 digits (`pipeline/weather.py`'s `_STATION_ID_RE` rejects
   anything else before it ever builds a fetch URL).
3. Note the `type` field. A four-parameter station (type `A`) reports
   precipitation, temperature, wind, and sunshine; other types report a
   narrower subset. `pipeline/weather.py` only reads precipitation and
   temperature, so any type that reports at least precipitation is usable —
   but a station that reports precipitation without temperature still yields
   rows with `temp_avg_c`/`temp_max_c`/`temp_min_c` left `NULL` rather than
   being rejected.
4. Use `kjName` as `agency_weather_stations.station_name` — it is what the
   rest of the pipeline (and this runbook's worked example) treats as the
   station's canonical name.

## Step 2 — find the agency_id

Run this read-only query yourself against the real Postgres (dev
`localhost:5433/transit`, or the production database) — do not run it as an
automated step, and never run a write query against either:

```sql
SELECT agency_id, agency_name FROM agencies WHERE agency_name ILIKE '%<name>%';
```

Note the `agency_id` from the result; it is the primary key
`agency_weather_stations` is keyed on.

## Step 3 — insert the mapping row

With the station id/name from Step 1 and the agency_id from Step 2, run:

```sql
INSERT INTO agency_weather_stations (agency_id, station_id, station_name, source, note)
VALUES (<agency_id>, '<station_id>', '<station_name>', 'jma_amedas', '<note>');
```

Notes on the columns:

- `source` should be `'jma_amedas'` — the only source `pipeline/weather.py`
  currently ingests (`WEATHER_SOURCE` in that module). Leaving it out also
  works since the column defaults to `'jma_amedas'`.
- `note` is **public**: `/weather_delay` returns it verbatim to every caller,
  authenticated or not. Write it as operator-facing prose explaining *why*
  this station represents this agency's service area (geography, siting) —
  never internal-only content such as contract terms, contact details, or
  staff names. It may be left `NULL`, but a station mapping without an
  explanation of what it's keyed to is harder for a reader to trust.
- `agency_id` is the primary key, so an agency can have only one
  representative station at a time. Several agencies may point at the same
  `station_id` — that's expected for operators covering the same city. To
  re-point an agency that already has a row, use `UPDATE` instead of
  `INSERT` (or the INSERT will fail on the primary key):

  ```sql
  UPDATE agency_weather_stations
  SET station_id = '<station_id>', station_name = '<station_name>', note = '<note>'
  WHERE agency_id = <agency_id>;
  ```

## Step 4 — enable the ingest kill switch

`WEATHER_INGEST_ENABLED` (documented in `.env.example` and `README.md`) is a
single global boolean, default `false`, gating every outbound fetch in
`pipeline/weather.py` — it is not per-agency. Scoping to specific agencies
happens naturally because the ingest pass only pulls the distinct
`station_id`s that already have a row in `agency_weather_stations`
(`pipeline/weather.py`'s `_STATIONS_SQL`); an agency with no row is simply
never fetched for, regardless of this switch.

Set in the deployment's environment (e.g. `.env` for local/dev, or the
production environment's variable store):

```
WEATHER_INGEST_ENABLED=true
```

This requires restarting the app process (or redeploying) so the new value is
read. Once enabled:

- The cron path (`api/routers/internal.py`'s `_run_weather_ingest`) picks up
  every configured station automatically on its normal schedule, bounded by
  `CRON_INGEST_BUDGET_SEC`.
- An operator can also backfill immediately with
  `make ingest-weather` (optionally `DAYS=<n>`, clamped to
  `pipeline.weather.PUBLICATION_WINDOW_DAYS`, currently 5 days — JMA does not
  publish observations older than that window, so a larger backfill request
  is silently clamped rather than erroring).

Turning the switch off again is a graceful no-op everywhere: both the cron
path and `make ingest-weather` log and skip, and `/weather_delay` keeps
reading whatever rows are already stored (it does not delete history).

## Worked example — 青森市バス pilot agency

1. **Station lookup**: `amedastable.json`'s `31312` entry has
   `"kjName": "青森"`, `"type": "A"` (reports precipitation, temperature,
   wind, and sunshine) — this is Aomori city's station, the right pick for
   an agency operating within Aomori city.
2. **Agency lookup**: an operator runs
   `SELECT agency_id, agency_name FROM agencies WHERE agency_name ILIKE '%青森市バス%';`
   against the real database and notes the returned `agency_id` (call it
   `<agency_id>` below — substitute the actual value from that query's
   result).
3. **Insert**:

   ```sql
   INSERT INTO agency_weather_stations (agency_id, station_id, station_name, source, note)
   VALUES (
       <agency_id>,
       '31312',
       '青森',
       'jma_amedas',
       '青森市バスの運行区域は青森市街地に集中しており、青森地方気象台（AMeDAS 31312・青森）が最も近い代表観測点のため採用。'
   );
   ```

4. **Enable ingest**: set `WEATHER_INGEST_ENABLED=true` and restart, or run
   `make ingest-weather` for an immediate backfill of the last
   `PUBLICATION_WINDOW_DAYS` days.

## Confirming it worked

All read-only:

- `SELECT * FROM weather_daily_observations WHERE station_id = '31312' ORDER BY obs_date DESC LIMIT 5;`
  should show rows appearing after the next cron tick or `make
  ingest-weather` run.
- `GET /api/{agency_id}/weather_delay` should return `available: true` (true
  once at least one in-range service day is matched to an observation)
  instead of `available: false`. Until enough days accumulate on both the
  wet and dry side (`MIN_DAYS_PER_GROUP` in `pipeline/reports/weather.py`),
  the response also carries `low_confidence: true` — expected right after
  enabling ingest, not a sign anything is broken.
- Application logs show `weather: wrote N of M station-days examined` (info
  level, from `pipeline/weather.py`'s `ingest_weather`) rather than `weather:
  WEATHER_INGEST_ENABLED is not set; skipping weather ingest`.

## Key files

| File | Role |
|---|---|
| `db/migrations/0042_weather_observations.up.sql` | `agency_weather_stations` / `weather_daily_observations` schema |
| `pipeline/weather.py` | Fetch/aggregate logic, `weather_ingest_enabled()` kill switch, `ingest_weather()` |
| `pipeline/reports/weather.py` | `/weather_delay`'s rain-vs-dry comparison read side |
| `api/routers/reports.py` | `GET /weather_delay` endpoint, `WeatherStation`/`WeatherDelayResponse` models |
| `api/routers/internal.py` | `_run_weather_ingest` — the cron path that calls `ingest_weather` |
| `gtfs_pipeline.py` | `cmd_ingest_weather` — the `ingest_weather` CLI subcommand behind `make ingest-weather` |
| `.env.example`, `README.md` | `WEATHER_INGEST_ENABLED` documentation |
