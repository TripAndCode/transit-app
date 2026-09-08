-- Per-route "minimum performance standard" configuration, powering the
-- internal bonus/malus simulation in pipeline.reports.performance_standard
-- (item 104). Manually populated by an operator, the same convention as
-- ridership_weights (migration 0035): a performance standard is a
-- negotiated policy input, not something derivable from GTFS/GTFS-RT data,
-- so there is no ingestion pipeline or CRUD API for this table.
--
-- metric_type selects which already-computed figure threshold_value is
-- compared against:
--   'ewt_sec'                  -- item 94's per-route Excess Waiting Time
--                                  (pipeline.reports.headway_quality),
--                                  in seconds, lower is better.
--   'vehicle_km_delivered_pct' -- item 98's vehicle-km-delivered rate
--                                  (pipeline.reports.supply), in percent
--                                  (0-100), higher is better. That figure
--                                  is only ever computed at agency
--                                  granularity (there is no per-trip
--                                  executed/canceled distance breakdown to
--                                  do better), so a row configured with
--                                  this metric_type is compared against its
--                                  own agency's rate, not a route-specific
--                                  one -- see the module docstring in
--                                  pipeline.reports.performance_standard for
--                                  how that scope difference is surfaced.
--
-- bonus_malus_rate is a magnitude, not a signed rate: it is the amount
-- awarded/deducted per 1.0 (100%) of relative deviation between the actual
-- figure and threshold_value. The sign of the resulting estimate comes
-- entirely from whether the actual figure beat or missed the standard (see
-- compute_performance_standards's formula), not from this column, so it is
-- constrained non-negative.
CREATE TABLE IF NOT EXISTS route_performance_standards (
    id                BIGSERIAL PRIMARY KEY,
    agency_id         INTEGER NOT NULL REFERENCES agencies(agency_id),
    route_code        TEXT NOT NULL,
    metric_type       TEXT NOT NULL CHECK (metric_type IN ('ewt_sec', 'vehicle_km_delivered_pct')),
    threshold_value   DOUBLE PRECISION NOT NULL,
    bonus_malus_rate  DOUBLE PRECISION NOT NULL CHECK (bonus_malus_rate >= 0),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- At most one standard per (agency, route, metric type) -- a route may have
-- both an EWT standard and a vehicle-km-delivered standard configured at
-- once, but not two conflicting thresholds for the same metric.
CREATE UNIQUE INDEX IF NOT EXISTS idx_route_performance_standards_agency_route_metric
    ON route_performance_standards (agency_id, route_code, metric_type);
