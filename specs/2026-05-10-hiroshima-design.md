# Hiroshima 3-operator GTFS-RT integration — design (2026-05-10)

Brings 広島電鉄 (8) / 広島バス (9) / 広島交通 (10) into the existing pipeline alongside 青森市バス (1). Same outputs as Aomori (delay/punctuality analytics, per-op). TripUpdate only in v1; VP and alerts deferred.

Verified against feasibility report `specs/2026-05-09-hiroshima-feasibility.md` and live Oracle VM (`opc@64.110.114.101`) inspection on 2026-05-09. Aomori on Oracle: `poller.sh` while-loop daemon at 30s interval, started via `@reboot` cron; static at 09:00 JST.

---

## Goals

- Add 3 Hiroshima operators on the same 30s cadence as Aomori.
- Same `analyze.py` / reports / API output shape, scoped per `agency_id`.
- Aomori output **byte-identical** to today after refactor (regression-locked).
- Crawler becomes config-driven (agencies.csv as the single registry).

## Non-goals (v1)

- VehiclePosition or Alerts ingestion (deferred).
- Combined cross-op rollups.
- Loading `calendar.txt` (today's `calendar_dates.txt` coverage suffices for service mapping; full active-on-date checks deferred).
- Any frontend redesign beyond plumbing `agency_id` through existing components where it isn't already.

---

## Architecture

One new abstraction: an **ingest strategy**. A strategy module under `pipeline/strategies/` exposes:

```python
def parse_feed(pb_bytes: bytes, agency_id: int, conn) -> list[UpdateRow]
```

`pipeline/ingest.py` becomes a thin router that (1) reads the agency row from `agencies.csv`, (2) loads the strategy by name, (3) hands it the pb bytes plus a DB connection (for JOIN-mode strategies), (4) bulk-inserts the returned rows into `updates`.

Two strategies after this change:

| name | applies to | how |
|---|---|---|
| `aomori_regex` | agency 1 | existing logic, lifted as-is. trip_id regex `^(?P<service>.+?)_(?P<hour>\d+)時(?P<minute>\d+)分_系統(?P<route>\d+)$` populates `route_code/service_type/scheduled_time` directly. Output bit-identical to today. |
| `static_join` | agencies 8/9/10 | PB decode only. Row enrichment via `INSERT…SELECT…LEFT JOIN static_trips, static_stop_times` scoped by `agency_id`. |

A symmetric pair of strategies for static GTFS fetch (parallel concept, separate registry):

| name | applies to | how |
|---|---|---|
| `aomori_index_scrape` | agency 1 | existing `poller_static.sh` HTML-index scrape + ZIP download + sha256 + history append. |
| `direct_url` | agencies 8/9/10 | conditional GET on `static_url` (HEAD via `If-Modified-Since`/ETag). Download `current_data.zip` and `latest.zip` separately, hash, prefer `latest` once it diverges. |

Both static strategies feed into the same downstream: persist a daily ZIP snapshot, then call `pipeline/static_loader.load_static`.

Crawler stays on Oracle VM. Reads agencies.csv (or a JSON exported from it at deploy) and drives both pollers from that registry. Pipeline code in this repo never directly fetches RT pb (preserved Aomori property).

---

## Data model

### agencies.csv (registry)

Add `ingest_strategy` and `static_strategy` columns. Keep `trip_id_pattern` as a strategy-private convenience for `aomori_regex` (could move into the strategy module later; left as-is for v1 to minimize Aomori diff).

```csv
agency_id,agency_name,feed_url,static_url,ingest_strategy,static_strategy,trip_id_pattern
1,青森市バス,https://aomoricitybus.com/TripUpdate.pb,https://aomoricitybus.com/opendata/index.html,aomori_regex,aomori_index_scrape,
8,広島電鉄,https://ajt-mobusta-gtfs.mcapps.jp/realtime/8/trip_updates.bin,https://ajt-mobusta-gtfs.mcapps.jp/static/8/current_data.zip,static_join,direct_url,
9,広島バス,https://ajt-mobusta-gtfs.mcapps.jp/realtime/9/trip_updates.bin,https://ajt-mobusta-gtfs.mcapps.jp/static/9/current_data.zip,static_join,direct_url,
10,広島交通,https://ajt-mobusta-gtfs.mcapps.jp/realtime/10/trip_updates.bin,https://ajt-mobusta-gtfs.mcapps.jp/static/10/current_data.zip,static_join,direct_url,
```

`static_url` for Aomori is the index page (preserves current scrape behavior); for Hiroshima it is the direct ZIP URL.

### Database

- `updates` — **schema unchanged**. Same columns, same widths. Both strategies write the same shape. `agency_id` is already the partition key.
- `static_*` tables — **schema unchanged**. Existing `static_loader.py` covers `trips/stop_times/routes/stops/calendar_dates/shapes`. Hiroshima static zips include extension files (`*_jp.txt`, `*_mobustation.txt`) which the loader ignores by virtue of its file map.
- No new tables in v1.

### Archive layout (Oracle VM)

Today: `archive/<YYYYMMDD>/TripUpdate_<HHMMSS>.pb` and `archive/<YYYYMMDD>.tar.gz` (no agency dimension).

Going forward: `archive/<agency_id>/<YYYYMMDD>/TripUpdate_<HHMMSS>.pb` and `archive/<agency_id>/<YYYYMMDD>.tar.gz`.

Migration of existing Aomori archives done as a one-shot rename in the cutover commit (covers both already-rolled tarballs `archive/<YYYYMMDD>.tar.gz` and the in-progress day directory `archive/<YYYYMMDD>/`):

```bash
mkdir -p archive/1
shopt -s nullglob
mv archive/2026[01]* archive/1/   # covers all 2026MMDD dirs and tarballs
```

`fetch_archives.sh` then changes its rsync include from `--include="*.tar.gz"` to `--include="*/" --include="*.tar.gz"` so it recurses one level into per-agency dirs.

Static archive layout migrates the same way: `static_archive/<agency_id>/gtfs_static_<YYYYMMDD>.zip`.

---

## `static_join` strategy internals

Module `pipeline/strategies/static_join.py`. Public fn signature unchanged:

```python
def parse_feed(pb_bytes, agency_id, conn) -> list[UpdateRow]
```

1. **PB decode** — reuse the varint helpers (`_read_varint/_read_ld/_fields/_dec`) lifted from current `ingest.py` into `pipeline/strategies/_pb.py`. Walk `entity → trip_update`. For each `stop_time_update` emit a flat dict with `(trip_id, rt_route_id, stop_sequence, stop_id_raw, arr_delay, arr_time, dep_delay, dep_time)`. Keep `stop_id` as-is — Hiroshima's `"<base> <platform>"` string is the same in both RT and static, so straight equality works.
2. **`captured_at`** — derived from pb filename `_HHMMSS.pb` plus the date inferred from the archive path's `<YYYYMMDD>` directory segment. Logic lifted from current `_ts()` helper into a shared util; updated to find the date by name-pattern (`re.fullmatch(r"\d{8}", segment)`) instead of fixed parent index, so it works under both the old `archive/<YYYYMMDD>/...` and the new `archive/<agency_id>/<YYYYMMDD>/...` layouts.
3. **Enrichment via SQL** — router writes raw rows to a `VALUES`/CTE and runs:

```sql
INSERT INTO updates (
  agency_id, captured_at, trip_id, route_code,
  service_type, scheduled_time, stop_id, stop_sequence,
  arr_delay, arr_time, dep_delay, dep_time
)
SELECT
  %(agency_id)s, %(captured_at)s, r.trip_id,
  r.rt_route_id,                              -- route_code straight from RT
  t.service_id,                               -- service_type via JOIN
  st.departure_time,                          -- scheduled_time via JOIN
  r.stop_id_raw, r.stop_sequence,
  r.arr_delay, r.arr_time, r.dep_delay, r.dep_time
FROM   raw_rows r
LEFT JOIN static_trips      t  ON t.agency_id  = %(agency_id)s AND t.trip_id  = r.trip_id
LEFT JOIN static_stop_times st ON st.agency_id = %(agency_id)s AND st.trip_id = r.trip_id
                              AND st.stop_sequence = r.stop_sequence;
```

4. **Failure modes**
   - Malformed PB → strategy logs and returns `[]`. Router commits zero rows for that snapshot.
   - Static row missing for a trip_id → `service_type/scheduled_time` are NULL for those rows (LEFT JOIN). Mismatch counter exposed in the per-snapshot ingest summary log line. Not fatal.
   - `analyze.py` already tolerates NULL service_type for the existing Aomori-edge cases; verify in the regression test.

---

## `static_fetcher.py` + `direct_url` strategy

New module `pipeline/static_fetcher.py`.

```python
def refresh_static(agency_id: int, conn, dest_dir: pathlib.Path) -> Optional[pathlib.Path]
```

Returns the path of the freshly-loaded zip, or `None` if no change.

Logic for `direct_url` strategy (Hiroshima):

1. Read `static_url` from agencies.csv. Empty → skip.
2. **Conditional GET** of `current_data.zip` and `latest.zip` separately, using `If-Modified-Since` / `If-None-Match` from a manifest at `<dest_dir>/<agency_id>/_manifest.json`:
   ```json
   {
     "current": {"url": "...", "last_modified": "...", "etag": "...", "sha256": "..."},
     "latest":  {"url": "...", "last_modified": "...", "etag": "...", "sha256": "..."}
   }
   ```
3. 304 response → no-op for that variant.
4. 200 with sha256 unchanged → manifest LM/ETag updated, no reload (server bumped headers spuriously).
5. 200 with sha256 new → persist zip to `static_archive/<agency_id>/gtfs_static_<YYYYMMDD>.zip`.
6. **Pick which to load**:
   - `latest.zip` sha differs from `current_data.zip` sha → load `latest.zip` (pre-cutover schedule).
   - Identical → load `current_data.zip`.
7. Call `load_static(zip_path, agency_id, conn)`. (Existing function. Unchanged.)
8. Write new manifest. Done.

Logic for `aomori_index_scrape` strategy (agency 1, today's behavior):

1. GET the index HTML.
2. `grep -Eo 'href="[^"]*gtfs-aomoricitybus[^"]*\.zip"'` (lifted from current `poller_static.sh`).
3. Resolve relative URL against `SITE_ROOT`.
4. Download zip → sha256 → persist → `load_static`. Same downstream as `direct_url`.

The Aomori scrape preserves today's behavior verbatim. Code lifted from `poller_static.sh` into Python under the strategy module; the shell version on Oracle goes away after cutover and is replaced by a one-line crontab calling `gtfs_pipeline.py refresh-static --agency-id 1`.

CLI surface in `gtfs_pipeline.py`:

```
poetry run python gtfs_pipeline.py refresh-static                     # all agencies with static_url
poetry run python gtfs_pipeline.py refresh-static --agency-id 8       # single op
```

Network failure returns None with a warning, exit code 0 (cron-friendly — flaky network shouldn't page).

---

## Crawler (Oracle VM, separate repo)

Two scripts redesigned to read agencies.csv at startup.

### RT crawler — replaces single-feed `poller.sh`

Today: one `while true; do curl <hardcoded_feed_url>; sleep 30; done` for Aomori only.

After: a process supervisor that spawns N child loops, one per agency where `feed_url` is non-empty, each running the same per-agency 30s curl loop into `archive/<agency_id>/<YYYYMMDD>/TripUpdate_<HHMMSS>.pb`. Day-rollover tar.gz logic stays per-agency.

Two viable shapes — pick at implementation time:

**(a) Single bash supervisor** — `poller.sh` reads `agencies.json` (exported from agencies.csv), iterates agencies, backgrounds one fetch loop per agency. Single `@reboot` cron entry unchanged. Simpler, less moving parts.

**(b) systemd template unit** — `gtfs-poller@.service` parameterized by `agency_id`. `systemctl enable gtfs-poller@1 gtfs-poller@8 gtfs-poller@9 gtfs-poller@10`. Cleaner, restartable per op, journald logs. Slightly more setup.

Recommend (a) for v1 — preserves Aomori's current `@reboot` cron pattern, smallest deploy delta. Migrate to (b) later if per-op restart becomes a thing we want.

`INTERVAL=30` constant. Keep the `MAX_RETRIES=4` exponential backoff per fetch.

### Static crawler — replaces `poller_static.sh`

Today: 09:00 JST cron runs `poller_static.sh`, scrapes HTML for Aomori only.

After: 09:00 JST cron runs:

```
poetry run python gtfs_pipeline.py refresh-static
```

…which iterates all agencies with `static_url` set and dispatches to the right static strategy (`aomori_index_scrape` for 1, `direct_url` for 8/9/10). Single cron entry replaces the script.

`fetch_history.csv` (timestamp, url, sha256, bytes, file_path) preserved as a per-agency history file at `static_archive/<agency_id>/fetch_history.csv`.

---

## Bandwidth and ops impact

Per 30s cycle, total RT fetch volume across all 4 ops at current sizes:

| Op | TU bytes |
|---|---:|
| 1 (Aomori) | ~6 KB |
| 8 (Hiroden) | ~52 KB |
| 9 (Hirobus) | ~19 KB |
| 10 (Hirokoh) | ~25 KB |
| **total** | **~102 KB / 30s** |

Sustained ~3.4 KB/s. Negligible.

Daily archive growth scales linearly with fetches × payload. Hiroshima ops produce ~7-15 MB/day per op tar.gz at current sizes. Disk on `/home/opc` should be checked before cutover; not expected to be tight.

---

## Testing

Three test groups added under existing `tests/` (pytest layout assumed; verified at plan-write time).

### 1. Aomori regression lock (must-pass before any Hiroshima code lands)

Fixture: existing Aomori `.pb` file + a snapshot of `updates` rows produced by the **current** `ingest.py` (golden CSV checked into `tests/fixtures/aomori_golden.csv`).

Test runs the post-refactor strategy-routed ingest on the fixture against an empty Postgres + loaded Aomori static. Asserts every column of every row matches the golden file. Locks Aomori behavior in CI.

This test must pass on the refactor commit before any Hiroshima code lands. Ships ahead of any agencies.csv changes for ops 8/9/10.

### 2. `static_join` unit tests (one per Hiroshima op)

Fixture per op (8, 9, 10): a captured `trip_updates.bin` + the matching static zip (re-derivable via `curl` from the live endpoints documented in feasibility).

Assertions per op:
- Row count equals the sum of `stop_time_updates` across entities in the pb fixture.
- 100% of rows have non-null `route_code` (RT always carries route_id for Hiroshima).
- ≥99% of rows have non-null `service_type` AND `scheduled_time` (from JOIN). Mismatch counter asserted ≤ a small per-fixture budget (≤1% of rows).
- `stop_id` round-trips with the `"<base> <platform>"` format intact.
- `captured_at` parsed correctly from `_HHMMSS.pb` filename.

### 3. `static_fetcher` tests

HTTP mocked with `responses` (no real network).

- `direct_url`: 304 → no-op, manifest unchanged.
- `direct_url`: 200 with identical sha256 → no reload, manifest LM/ETag updated.
- `direct_url`: 200 with new sha256 → zip persisted, `load_static` called once.
- `direct_url`: `latest.zip` sha differs from `current_data.zip` sha → loads `latest`.
- `direct_url`: network failure → returns None, exit code 0, warning emitted.
- `aomori_index_scrape`: index page parses, ZIP href resolved, downloaded, hashed.

### 4. End-to-end smoke (one per op)

`refresh-static` → ingest one TU snapshot → `analyze.py daily-by-route` returns rows. Catches schema drift.

---

## Rollout sequence

Six commits, each independently revertable. Aomori production behavior unaffected until step 5.

| # | What | Where | Risk |
|---|---|---|---|
| 1 | Refactor Aomori into `pipeline/strategies/aomori_regex.py`. Add `ingest_strategy` column to agencies.csv. `ingest.py` becomes router. **Aomori regression lock test green.** Deploy to Oracle, observe ≥1 day. | this repo | low — pure refactor, locked test |
| 2 | Add `pipeline/strategies/static_join.py`. Add agency 8 row to agencies.csv with `ingest_strategy=static_join`. Manually `load_static` a Hiroden zip into staging DB. Run unit tests. | this repo | low — staging only |
| 3 | Add `pipeline/static_fetcher.py` + `direct_url` and `aomori_index_scrape` static strategies. CLI subcommand. Tests green. Run for agency 8 to land its zip via the new path. | this repo | low |
| 4 | Add agencies 9 + 10 rows to agencies.csv. Run `refresh-static --agency-id 9` and `--agency-id 10`. Run static_join unit tests against captured TU fixtures. | this repo | low — config only |
| 5 | Crawler config (Oracle VM, separate repo): redesign `poller.sh` as multi-agency supervisor reading agencies.json. Replace `poller_static.sh` with cron calling `gtfs_pipeline.py refresh-static`. One-shot move of existing `archive/2026*` into `archive/1/`. Deploy. | crawler repo + Oracle | medium — touches running crawler |
| 6 | Wire RT ingest in production for agencies 8/9/10. Confirm `updates` rows accumulating. Run `analyze.py` per agency_id. Verify report shape matches Aomori. Plumb `agency_id` through any frontend route param that doesn't have it yet. | this repo | medium — production data |

Effort: ~2 dev-days in this repo + crawler ops (~0.5 day) on Oracle.

---

## Risks and open items

- **Step 5 cutover** is the only step that touches the running crawler. Plan a brief window (<5 min Aomori RT gap acceptable; falls within the existing retry envelope of analyze).
- **`static_url` for Aomori** — Aomori row gets the index page URL today (preserves current scrape). If it ever moves to direct ZIP, swap `aomori_index_scrape` → `direct_url`. Trivial.
- **Calendar.txt** — not loaded today. If we want service-active-on-date validation post-MVP, add it to `static_loader.py`'s file map (one-line addition).
- **Materialized enriched view** — if static GTFS revisions ever require rewriting historical `updates` denormalization, a future commit promotes from "eager at insert" to "raw + materialized view" (option C from brainstorm). Boundary already exists at the strategy module — no schema rewrite needed in v1.
- **VP/alerts** — deferred. When prioritized: VP needs a new `vehicle_positions` table; alerts need a real protobuf decoder (`gtfs-realtime-bindings` PyPI dep, ~50 LoC).
- **post-quantum SSH banner** — Oracle VM emits a deprecation warning on each SSH session. Out of scope for this design; logged as a separate follow-up.
