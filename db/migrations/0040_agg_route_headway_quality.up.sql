-- Headway QUALITY metrics (item 94, depends on item 93's agg_route_headway /
-- agg_route_headway_daily): Excess Waiting Time, coefficient of variation,
-- and long-gap rate for routes `agg_route_headway.is_high_frequency`
-- classifies as high-frequency. See `pipeline.headways` for the shared
-- formulas and `pipeline.reports.headway_quality` for the query-time
-- pooling these columns feed.
--
-- scheduled_wait_mean_sec (agg_route_headway) is E[H^2] / (2*E[H]) over the
-- STATIC schedule's own headway distribution -- the same mean-wait formula
-- applied to actual RT headways below, computed once per route since the
-- schedule doesn't vary day to day.
--
-- actual_headway_sum_sec / actual_headway_sumsq_sec2 (agg_route_headway_daily)
-- are the per-day SUM(gap) and SUM(gap^2) behind that day's existing
-- actual_headway_median_sec -- sufficient statistics that stay additive
-- across days (summing each day's own triple equals computing the same
-- formula over every day's gaps concatenated), so a reader pooling multiple
-- days sums these once rather than needing the raw per-day gap lists back.
-- long_gap_count is that day's count of gaps exceeding
-- `pipeline.headways.LONG_GAP_MULTIPLIER` times the route's
-- scheduled_headway_median_sec (0, not NULL, when no scheduled median was
-- resolvable for the route at analyze time -- such a route is never
-- high-frequency and never surfaced by the query-time report anyway).
--
-- All four nullable with no default/backfill, matching migration 0028's
-- precedent for adding a column analyze() populates going forward only:
-- analyze() unconditionally DELETEs + re-INSERTs every row for an agency, so
-- backfilling here would just be discarded by the next `make analyze-all`
-- (required after this migration regardless, per this repo's aggregate-
-- rebuild convention). Every reader must treat these as nullable until that
-- rebuild completes.
ALTER TABLE agg_route_headway ADD COLUMN IF NOT EXISTS scheduled_wait_mean_sec REAL;
ALTER TABLE agg_route_headway_daily ADD COLUMN IF NOT EXISTS actual_headway_sum_sec REAL;
ALTER TABLE agg_route_headway_daily ADD COLUMN IF NOT EXISTS actual_headway_sumsq_sec2 REAL;
ALTER TABLE agg_route_headway_daily ADD COLUMN IF NOT EXISTS long_gap_count INTEGER;
