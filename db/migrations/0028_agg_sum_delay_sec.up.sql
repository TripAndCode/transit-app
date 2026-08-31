-- Six agg_* tables stored only a pre-rounded (2 dp minutes, or whole seconds for
-- agg_route_daily) per-bucket mean, with no raw-seconds sum alongside it. Every
-- downstream consumer that re-pools MULTIPLE buckets of one of these tables
-- (SUM(avg_min * samples) / SUM(samples), or SUM(avg_delay_sec * samples) /
-- SUM(samples) for agg_route_daily) was therefore computing a sample-weighted
-- mean of ALREADY-ROUNDED per-bucket values, not of the underlying raw delay
-- data — a small but real, systematic divergence from the true pooled mean
-- (and from the live/ClickHouse path, which averages raw seconds directly).
--
-- sum_delay_sec is the exact SUM(dep_delay) in seconds behind each row's
-- avg_min/avg_delay_sec, mirroring agg_route_daily_dist's sum_delay_sec (which
-- has always been exact). A downstream pooling SUM(sum_delay_sec) / SUM(samples)
-- once, at the end, is now bit-for-bit equal to a from-scratch AVG(dep_delay)
-- over the same raw rows — dividing once instead of re-weighting an
-- intermediate rounded average.
--
-- Nullable on every table, including agg_route_daily (whose avg_delay_sec/
-- samples are themselves NOT NULL, migration 0018) — this column is populated
-- going forward by pipeline/analyze.py, not by this migration, so it must
-- tolerate being NULL on any row analyze() hasn't rewritten yet.
--
-- Deliberately NOT backfilled: analyze() unconditionally DELETEs + re-INSERTs
-- every agg_* row for an agency, so any value this migration computed here
-- would carry the SAME rounding error the fix eliminates going forward and
-- get immediately discarded by the next `make analyze-all` (which must run
-- after this migration regardless, per this repo's own aggregate-rebuild
-- convention). A bare ADD COLUMN with no default is a metadata-only change on
-- Postgres — skipping the backfill avoids rewriting all six tables, and
-- skipping NOT NULL avoids a table lock held for a validating full-table scan
-- and a window where analyze() code older than this migration would violate
-- the constraint. Every reader of sum_delay_sec must already treat it as
-- nullable until `make analyze-all` completes.
ALTER TABLE agg_route_stats ADD COLUMN IF NOT EXISTS sum_delay_sec BIGINT;
ALTER TABLE agg_route_hour ADD COLUMN IF NOT EXISTS sum_delay_sec BIGINT;
ALTER TABLE agg_route_dow ADD COLUMN IF NOT EXISTS sum_delay_sec BIGINT;
ALTER TABLE agg_route_hour_dow ADD COLUMN IF NOT EXISTS sum_delay_sec BIGINT;
ALTER TABLE agg_daily_trend ADD COLUMN IF NOT EXISTS sum_delay_sec BIGINT;
ALTER TABLE agg_route_daily ADD COLUMN IF NOT EXISTS sum_delay_sec BIGINT;
