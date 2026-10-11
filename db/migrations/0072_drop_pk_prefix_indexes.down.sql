-- Recreates the redundant prefix indexes (rebuilt from table data; nothing is lost).
CREATE INDEX IF NOT EXISTS idx_agg_route_stop_daily_agency_route
    ON agg_route_stop_daily (agency_id, route_code);
CREATE INDEX IF NOT EXISTS idx_sst_trip ON static_stop_times (agency_id, trip_id);
