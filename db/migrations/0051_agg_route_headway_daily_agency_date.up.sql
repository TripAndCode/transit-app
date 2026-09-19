-- Let the per-date purge find its rows instead of filtering for them.
--
-- Same shape as the index on agg_route_stop_daily: analyze() deletes this
-- table's rows for a specific set of service dates, and `date` sits last in
-- the primary key behind route_code, so the planner can narrow to the agency
-- but must then walk every route within it to find the dates. This is the
-- last table in the incremental set that could not answer (agency_id, date)
-- from an index; tests/pipeline/test_analyze.py asserts that none can regress
-- to that state.
CREATE INDEX IF NOT EXISTS idx_agg_route_headway_daily_agency_date
    ON agg_route_headway_daily (agency_id, date);
