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
-- Nullable to match each table's existing avg_min/samples nullability (all were
-- left nullable back in migration 0001, even though a GROUP BY row always has
-- >= 1 sample in practice) — except agg_route_daily, whose avg_delay_sec/samples
-- were declared NOT NULL from the start (migration 0018), so sum_delay_sec
-- follows suit there.
--
-- Backfilled from the existing (rounded) avg_min/avg_delay_sec so the column is
-- never silently NULL against a non-NULL samples count before the next
-- analyze() run (which unconditionally DELETEs + re-INSERTs every agg_* row
-- for an agency, so this backfill is only ever a stopgap for the
-- this-migration to next-analyze() window). The backfilled value carries the
-- SAME rounding error the fix eliminates going forward — analyze() must be
-- re-run (`make analyze-all`) per agency for sum_delay_sec to become exact.
ALTER TABLE agg_route_stats ADD COLUMN IF NOT EXISTS sum_delay_sec BIGINT;
UPDATE agg_route_stats SET sum_delay_sec = ROUND(avg_min::numeric * samples * 60)
    WHERE sum_delay_sec IS NULL AND avg_min IS NOT NULL AND samples IS NOT NULL;

ALTER TABLE agg_route_hour ADD COLUMN IF NOT EXISTS sum_delay_sec BIGINT;
UPDATE agg_route_hour SET sum_delay_sec = ROUND(avg_min::numeric * samples * 60)
    WHERE sum_delay_sec IS NULL AND avg_min IS NOT NULL AND samples IS NOT NULL;

ALTER TABLE agg_route_dow ADD COLUMN IF NOT EXISTS sum_delay_sec BIGINT;
UPDATE agg_route_dow SET sum_delay_sec = ROUND(avg_min::numeric * samples * 60)
    WHERE sum_delay_sec IS NULL AND avg_min IS NOT NULL AND samples IS NOT NULL;

ALTER TABLE agg_route_hour_dow ADD COLUMN IF NOT EXISTS sum_delay_sec BIGINT;
UPDATE agg_route_hour_dow SET sum_delay_sec = ROUND(avg_min::numeric * samples * 60)
    WHERE sum_delay_sec IS NULL AND avg_min IS NOT NULL AND samples IS NOT NULL;

ALTER TABLE agg_daily_trend ADD COLUMN IF NOT EXISTS sum_delay_sec BIGINT;
UPDATE agg_daily_trend SET sum_delay_sec = ROUND(avg_min::numeric * samples * 60)
    WHERE sum_delay_sec IS NULL AND avg_min IS NOT NULL AND samples IS NOT NULL;

-- avg_delay_sec is already whole seconds (not minutes), so no *60 here.
ALTER TABLE agg_route_daily ADD COLUMN IF NOT EXISTS sum_delay_sec BIGINT;
UPDATE agg_route_daily SET sum_delay_sec = ROUND(avg_delay_sec::numeric * samples)
    WHERE sum_delay_sec IS NULL;
ALTER TABLE agg_route_daily ALTER COLUMN sum_delay_sec SET NOT NULL;
