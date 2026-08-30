---
name: postgres-perf
description: Performance patterns and known traps for this repo's Postgres/PostGIS/pgvector DB and ClickHouse `updates` store. Use when optimizing queries, adding aggregates, or diagnosing slow endpoints.
---

# Postgres + ClickHouse performance — transit-app

Postgres 16 + PostGIS + pgvector + pg_trgm holds `agg_*`/OLTP/PostGIS/pgvector data.
The raw GTFS-RT `updates` fact table (hundreds of millions of rows across 4
agencies, and growing) lives in ClickHouse
instead (migrated from Postgres; the old Postgres `updates` table still exists as
a rollback safety net but has zero production readers). MergeTree/partition-key/
ORDER-BY-key advice DOES apply to `updates` — its `ORDER BY (agency_id,
captured_at, route_code, trip_id, stop_sequence)` is why route-scoped probes need
a date bound (see below) and why `route_code` needed `allow_nullable_key=1` to
become Nullable.

## Proven patterns
- Daily aggregates beat per-row scans. Slow analytical endpoints were fixed by
  materializing per-day aggregate tables (`agg_stop_daily`, `agg_route_daily`,
  `agg_route_stop_daily`, `agg_feed_health`, …) instead of scanning raw
  observations — a per-day aggregate turns an O(rows) scan into an O(days) read.
  Build aggregates in the analyze step. The default (`time_band=all`, no
  service/route filter) request on every
  read endpoint serves from an `agg_*` table — but a `time_band`/custom-threshold/
  narrow-ctx filter falls back to a LIVE ClickHouse scan of `updates` (see
  `pipeline/reports/filters.py::_dedup_cte_ch`); those live-fallback paths are
  where a new perf trap is most likely to show up first, not the fast path.
- Materialize the dedup ONCE per agency. `analyze()` builds `_analyze_deduped` (a
  Postgres TEMP table, `ON COMMIT DROP`) from ClickHouse via
  `pipeline/db.py::build_dedup_ch_sql`, streamed in blocks (not `.query()`, which
  buffers the whole result in memory, scaling with the agency's row count), and
  every builder reads that temp table instead of re-scanning ClickHouse.
  Exception: `agg_stop_routes` reads a SEPARATE unfiltered ClickHouse scan
  (`_analyze_raw_keys`) — `_analyze_deduped` is pre-filtered by the delay clamp
  below, which would silently drop stops whose every observation was
  NULL/implausible delay (a real, non-trivial share of keys).
- Data-quality clamp lives in `build_dedup_ch_sql` (`MAX_PLAUSIBLE_DELAY_SEC`,
  120min): frozen/stale-feed delay spikes (e.g. 976min) are dropped before any
  averaging, so they can't skew means/counts on any surface.
- ClickHouse route-scoped probes MUST be date-bounded. `route_code` is the 3rd
  sort-key column behind an unconstrained `captured_at`, so an unbounded
  `WHERE route_code = ...` forces a full-partition scan (hundreds of millions
  of rows, hundreds of milliseconds to low seconds) even for a route that
  doesn't exist. `api/routers/map.py`'s
  route_trips/route_stop_profile/route_shape bound to
  `max_captured_at(ch, agency_id) - 30 days` — a route ingested but not yet
  analyzed is by definition within the last cron cycle, so 30 days loses
  nothing real for those. `pipeline/query/tools.py`'s `_is_route_registered`
  needs a DIFFERENT bound: a fixed 30-day window there would report a real,
  merely-idle route as unregistered (see its docstring), so it derives the
  bound from `agg_route_daily`'s own analyze horizon for the agency instead —
  and when the agency has no `agg_route_daily` rows at all yet (no horizon to
  derive), it scans unbounded with an execution-time cap and fails OPEN on
  timeout rather than bounding by a guessed constant.
- Prefer `ORDER BY captured_at DESC LIMIT 1` over `maxOrNull(captured_at)` for a
  single-agency max: `captured_at` is the 2nd sort-key column, so the `LIMIT 1`
  form is index-served while the aggregate form is a full per-agency scan.
  See `api/clickhouse.py::max_captured_at_before`.

## Known traps
- `GROUP BY` binds the input column, not the output alias. A COALESCE-sentinel
  aggregate (e.g. `COALESCE(service_type, '∅')`) MUST `GROUP BY` the same COALESCE
  expression — grouping by the bare column duplicates the PK and aborts analyze.
- Sargable rewrites don't always help. The "make the predicate index-friendly"
  quick win FAILED here due to agency×captured_at correlation — the planner's row
  estimate is off regardless. Benchmark before assuming an index/sargable win.
- NULL `service_type` was silently dropped from typed aggregates until explicitly
  handled. `route_code` is Nullable too (both ClickHouse and
  the underlying GTFS-RT feeds) — check whether a new aggregate/live-fallback
  query needs the same COALESCE-sentinel or explicit-filter treatment.
- ClickHouse's `quantileExact`/`round()` do NOT reproduce Postgres semantics.
  `quantileExact` is a positional pick (`sorted[floor(q*n)]`); Postgres's
  `PERCENT_RANK()` uses min-rank ties — they silently disagree whenever the
  column has ties (common: `dep_delay` is dominated by exact-zero and
  clamped/rounded values). `round()` is round-half-to-even vs Postgres's
  round-half-away-from-zero. See `pipeline/reports/rankings.py::_ranking_live`
  (rank()/count() window functions) and its `_round2`/`_round1` helpers.
- A ClickHouse `Array(String)` parameter cannot contain `None` — a Nullable
  column's NULL value must be filtered out in Python before it reaches a
  `col IN {param:Array(String)}` binding, or the query raises a DatabaseError.

## DB safety
- Dev Postgres `:5433` (`transit-pg`) is READ-ONLY: EXPLAIN/SELECT only.
- Dev ClickHouse (`transit-ch`, hundreds of millions of real rows across 4
  agencies) is ALSO READ-ONLY for anything outside `make ch-bootstrap`: no
  manual `INSERT`/`ALTER`/`DROP` against it. `db/clickhouse/bootstrap.py` documents the one-time
  `ALTER TABLE ... MODIFY COLUMN` needed to bring its column types in sync with
  `db/clickhouse/schema.sql` — that's the one sanctioned exception.
- Tests run against throwaway Postgres `:5544` (built from `db/`, needs
  PostGIS+pgvector+pg_trgm) AND throwaway ClickHouse `:8124` (`make ch-test`).
  `RUN_CH_INTEGRATION=1` + the `CLICKHOUSE_*` env vars gate the ClickHouse-touching
  tests — see `transit-app-gotchas` for the exact env block.
- The API's async ClickHouse client (`api/clickhouse.py::get_ch_client`) runs
  `readonly=2` — it only ever `SELECT`s, every write/DDL path goes through
  `pipeline/clickhouse.py`'s sync client instead. It's a per-request default on
  that client object, not server-side enforcement — a future call site passing
  its own `settings={...}` to `ch.query(...)` can still lift it; a genuinely
  read-only CH user/profile is the only thing that would survive that.
- Both client factories take a `CLICKHOUSE_SECURE` env var (default `false`,
  plaintext HTTP) — set it for any non-local ClickHouse, and move
  `CLICKHOUSE_PORT` to match (e.g. 8443) since an explicit port defeats
  clickhouse-connect's own port-based TLS inference.
- `api/routers/internal.py`'s cron ingest+analyze job and `gtfs_pipeline.py`'s
  CLI ingest/ingest_live/analyze/analyze_all commands all take the same
  shared Postgres advisory lock (`pipeline.locks.INGEST_ANALYZE_LOCK_KEY`),
  narrowing (not closing) the window for a double poke, or a poke
  overlapping a scheduled CLI run, to double-`ingest_live` every agency
  (ClickHouse has no `ON CONFLICT DO NOTHING`). Per-CLI-invocation, not
  job-level: production runs `ingest`/`load_static`/`analyze` as separate
  per-agency processes, each independently acquiring/releasing the lock, so
  a poke can still land *between* two of them. On a miss, `ingest`/`analyze`
  log a warning and exit `EX_TEMPFAIL` (75) — self-healing, since a hard
  exit(1) there would abort the whole remaining per-agency shell loop —
  while `analyze_all`/`ingest_live` (nothing shell-loops over those) still
  fail loudly with exit 1, per their documented contract.
- Benchmark via `PERF_DEBUG_ENABLED` + `scripts/perf_bench.py`.
