-- Manually-populated, optional ridership weights, powering the
-- ridership-weighted on-time rate in pipeline.reports.ridership. No IC-card
-- or APC ridership data source exists in this pipeline -- an operator fills
-- this table in by hand with RELATIVE weights (any positive scale; only
-- ratios between rows matter, not the absolute unit) instead of the feature
-- blocking on acquiring real ridership data.
--
-- A row with route_code NULL is that agency's default weight, applied to any
-- route the agency hasn't given its own row; a row with route_code set
-- overrides the default for that one route. An agency with NO rows at all
-- (neither a default nor any route-specific row) has no ridership weighting
-- configured -- callers must read that as "not available", never fall back
-- to a silent uniform weight of 1 dressed up as "weighted" (see
-- pipeline.reports.ridership.agency_has_ridership_weights).
--
-- No stop_id column: no per-stop on-time aggregate exists in this pipeline
-- (agg_route_daily_dist's own grain stops at route/service/date), so a
-- stop-level weight would have nothing to multiply against.
CREATE TABLE IF NOT EXISTS ridership_weights (
    id          BIGSERIAL PRIMARY KEY,
    agency_id   INTEGER NOT NULL REFERENCES agencies(agency_id),
    route_code  TEXT,
    weight      NUMERIC NOT NULL CHECK (weight > 0),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- At most one default (route_code IS NULL) row per agency.
CREATE UNIQUE INDEX IF NOT EXISTS idx_ridership_weights_agency_default
    ON ridership_weights (agency_id)
    WHERE route_code IS NULL;

-- At most one row per (agency, route).
CREATE UNIQUE INDEX IF NOT EXISTS idx_ridership_weights_agency_route
    ON ridership_weights (agency_id, route_code)
    WHERE route_code IS NOT NULL;
