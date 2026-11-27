-- The analysis method (pipeline.agg_registry.METHOD_VERSION) in force when
-- the prune recorded the drop; NULL on a row recorded before the column
-- existed, whose method is unknown.
ALTER TABLE updates_pruned_months ADD COLUMN IF NOT EXISTS method_version TEXT;
