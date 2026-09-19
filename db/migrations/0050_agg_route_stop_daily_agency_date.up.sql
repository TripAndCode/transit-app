-- Let the per-date purge find its rows instead of filtering for them.
--
-- analyze() deletes this table's rows for a specific set of service dates
-- before rebuilding them. `date` sits fourth in the primary key, behind
-- route_code and stop_id, so the only usable index for that predicate was
-- (agency_id, route_code): the planner reads every one of the agency's rows
-- and discards the ones outside the date set, which on a table at
-- route × stop × day × service × time-band grain is the large majority of
-- them. Its sibling agg_stop_daily — the same grain minus route_code — has
-- carried the equivalent index since it was created.
CREATE INDEX IF NOT EXISTS idx_agg_route_stop_daily_agency_date
    ON agg_route_stop_daily (agency_id, date);
