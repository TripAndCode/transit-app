-- The analysis method (pipeline.agg_registry.METHOD_VERSION) in force when the
-- prune recorded the drop. The dates the drop made final keep the aggregates
-- built under it, since the prune holds a month until analyze has applied
-- every builder change, and an export reaching those dates names it.
-- NULL on a row recorded before the column existed: the method is unknown.
ALTER TABLE updates_pruned_months ADD COLUMN IF NOT EXISTS method_version TEXT;
