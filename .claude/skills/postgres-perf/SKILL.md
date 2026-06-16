---
name: postgres-perf
description: Performance patterns and known traps for this repo's Postgres/PostGIS/pgvector DB. Use when optimizing queries, adding aggregates, or diagnosing slow endpoints.
---

# Postgres performance — transit-app

Runs Postgres 16 + PostGIS + pgvector + pg_trgm. There is no ClickHouse — ignore
MergeTree/partition-key/ORDER-BY-key advice; it does not apply here.

## Proven patterns
- Daily aggregates beat per-row scans. Slow analytical endpoints were fixed by
  materializing per-day aggregate tables (`agg_stop_daily`, `agg_route_daily`,
  `agg_route_stop_daily`, `agg_feed_health`, …) instead of scanning raw
  observations. a1 5.8s→0.09s, a8 35–48s→0.24s. Build aggregates in the analyze
  step. EVERY read endpoint now serves from an `agg_*` table — there are no live
  `updates` scans left (the route-filtered heatmap was the last one, removed when
  `agg_route_stop_daily` landed). Add a report by adding/extending an aggregate.
- Materialize the dedup ONCE per agency. `analyze` builds `_analyze_deduped` (a
  TEMP table, `ON COMMIT DROP`) from `build_dedup_inner_sql` and every builder
  reads it, instead of 9+ full-partition dedup scans. New stop/route aggregates
  should read this temp too (not raw `updates`) so counts = observations not polls.
- Data-quality clamp lives in `build_dedup_inner_sql` (`MAX_PLAUSIBLE_DELAY_SEC`,
  120min): frozen/stale-feed delay spikes (e.g. 976min) are dropped before any
  averaging, so they can't skew means/counts on any surface.

## Known traps
- `GROUP BY` binds the input column, not the output alias. A COALESCE-sentinel
  aggregate (e.g. `COALESCE(service_type, '∅')`) MUST `GROUP BY` the same COALESCE
  expression — grouping by the bare column duplicates the PK and aborts analyze.
- Sargable rewrites don't always help. The "make the predicate index-friendly"
  quick win FAILED here due to agency×captured_at correlation — the planner's row
  estimate is off regardless. Benchmark before assuming an index/sargable win.
- NULL `service_type` was silently dropped from typed aggregates until explicitly
  handled (real bug, PR #60).

## DB safety
- Dev DB `:5433` (`transit-pg`, ~34M real `updates` rows / ~11GB) is READ-ONLY: EXPLAIN/SELECT only.
- Tests run against throwaway `:5544` built from `db/` (needs PostGIS+pgvector+pg_trgm).
- Benchmark via `PERF_DEBUG_ENABLED` + `scripts/perf_bench.py`.
